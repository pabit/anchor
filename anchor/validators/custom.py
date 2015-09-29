#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from __future__ import absolute_import

import logging

import netaddr
from pyasn1.type import univ as pyasn1_univ

from anchor.validators import errors as v_errors
from anchor.validators import utils
from anchor.X509 import errors
from anchor.X509 import extension
from anchor.X509 import name as x509_name


logger = logging.getLogger(__name__)


def common_name(csr, allowed_domains=[], allowed_networks=[], **kwargs):
    """Check the CN entry is a known domain.

    Refuse requests for certificates if they contain multiple CN
    entries, or the domain does not match the list of known suffixes.
    """
    alt_present = any(ext.get_name() == "subjectAltName"
                      for ext in csr.get_extensions())

    CNs = csr.get_subject().get_entries_by_oid(x509_name.OID_commonName)

    if len(CNs) > 1:
        raise v_errors.ValidationError("Too many CNs in the request")

    # rfc5280#section-4.2.1.6 says so
    if len(CNs) == 0 and not alt_present:
        raise v_errors.ValidationError("Alt subjects have to exist if the main"
                                       " subject doesn't")

    if len(CNs) > 0:
        cn = utils.csr_require_cn(csr)
        try:
            # is it an IP rather than domain?
            ip = netaddr.IPAddress(cn)
            if not (utils.check_networks(ip, allowed_networks)):
                raise v_errors.ValidationError(
                    "Address '%s' not allowed (does not match known networks)"
                    % cn)
        except netaddr.AddrFormatError:
            if not (utils.check_domains(cn, allowed_domains)):
                raise v_errors.ValidationError(
                    "Domain '%s' not allowed (does not match known domains)"
                    % cn)


def alternative_names(csr, allowed_domains=[], **kwargs):
    """Check known domain alternative names.

    Refuse requests for certificates if the domain does not match
    the list of known suffixes, or network ranges.
    """

    for _, name in utils.iter_alternative_names(csr, ['DNS']):
        if not utils.check_domains(name, allowed_domains):
            raise v_errors.ValidationError("Domain '%s' not allowed (doesn't"
                                           " match known domains)" % name)


def alternative_names_ip(csr, allowed_domains=[], allowed_networks=[],
                         **kwargs):
    """Check known domain and ip alternative names.

    Refuse requests for certificates if the domain does not match
    the list of known suffixes, or network ranges.
    """

    for name_type, name in utils.iter_alternative_names(csr,
                                                        ['DNS', 'IP Address']):
        if name_type == 'DNS' and not utils.check_domains(name,
                                                          allowed_domains):
            raise v_errors.ValidationError("Domain '%s' not allowed (doesn't"
                                           " match known domains)" % name)
        if name_type == 'IP Address':
            if not utils.check_networks(name, allowed_networks):
                raise v_errors.ValidationError("IP '%s' not allowed (doesn't"
                                               " match known networks)" % name)


def blacklist_names(csr, domains=[], **kwargs):
    """Check for blacklisted names in CN and altNames."""

    if not domains:
        logger.warning("No domains were configured for the blacklist filter, "
                       "consider disabling the step or providing a list")
        return

    CNs = csr.get_subject().get_entries_by_oid(x509_name.OID_commonName)
    if len(CNs) > 0:
        cn = utils.csr_require_cn(csr)
        if utils.check_domains(cn, domains):
            raise v_errors.ValidationError("Domain '%s' not allowed "
                                           "(CN blacklisted)" % cn)

    for _, name in utils.iter_alternative_names(csr, ['DNS'],
                                                fail_other_types=False):
        if utils.check_domains(name, domains):
            raise v_errors.ValidationError("Domain '%s' not allowed "
                                           "(alt blacklisted)" % name)


def server_group(auth_result=None, csr=None, group_prefixes={}, **kwargs):
    """Check Team prefix.

    Make sure that for server names containing a team prefix, the team is
    verified against the groups the user is a member of.
    """

    cn = utils.csr_require_cn(csr)
    parts = cn.split('-')
    if len(parts) == 1 or '.' in parts[0]:
        return  # no prefix

    if parts[0] in group_prefixes:
        if group_prefixes[parts[0]] not in auth_result.groups:
            raise v_errors.ValidationError(
                "Server prefix doesn't match user groups")


def extensions(csr=None, allowed_extensions=[], **kwargs):
    """Ensure only accepted extensions are used."""
    exts = csr.get_extensions() or []
    for ext in exts:
        if (ext.get_name() not in allowed_extensions and
                str(ext.get_oid()) not in allowed_extensions):
            raise v_errors.ValidationError("Extension '%s' not allowed"
                                           % ext.get_name())


def key_usage(csr=None, allowed_usage=None, **kwargs):
    """Ensure only accepted key usages are specified."""
    allowed = set(extension.LONG_KEY_USAGE_NAMES.get(x, x) for x in
                  allowed_usage)
    denied = set()

    for ext in (csr.get_extensions() or []):
        if isinstance(ext, extension.X509ExtensionKeyUsage):
            usages = set(ext.get_all_usages())
            denied = denied | (usages - allowed)
    if denied:
        raise v_errors.ValidationError("Found some prohibited key usages: %s"
                                       % ', '.join(denied))


def ext_key_usage(csr=None, allowed_usage=None, **kwargs):
    """Ensure only accepted extended key usages are specified."""

    # transform all possible names into oids we actually check
    for i, usage in enumerate(allowed_usage):
        if usage in extension.EXT_KEY_USAGE_NAMES_INV:
            allowed_usage[i] = extension.EXT_KEY_USAGE_NAMES_INV[usage]
        elif usage in extension.EXT_KEY_USAGE_SHORT_NAMES_INV:
            allowed_usage[i] = extension.EXT_KEY_USAGE_SHORT_NAMES_INV[usage]
        else:
            try:
                oid = pyasn1_univ.ObjectIdentifier(usage)
                allowed_usage[i] = oid
            except Exception:
                raise v_errors.ValidationError("Unknown usage: %s" % (usage,))

    allowed = set(allowed_usage)
    denied = set()

    for ext in csr.get_extensions(extension.X509ExtensionExtendedKeyUsage):
        usages = set(ext.get_all_usages())
        denied = denied | (usages - allowed)
    if denied:
        text_denied = [extension.EXT_KEY_USAGE_SHORT_NAMES.get(x)
                       for x in denied]
        raise v_errors.ValidationError("Found some prohibited key usages: %s"
                                       % ', '.join(text_denied))


def ca_status(csr=None, ca_requested=False, **kwargs):
    """Ensure the request has/hasn't got the CA flag."""
    request_ca_flags = False
    for ext in (csr.get_extensions() or []):
        if isinstance(ext, extension.X509ExtensionBasicConstraints):
            if ext.get_ca():
                if not ca_requested:
                    raise v_errors.ValidationError(
                        "CA status requested, but not allowed")
                request_ca_flags = True
        elif isinstance(ext, extension.X509ExtensionKeyUsage):
            has_cert_sign = ext.get_usage('keyCertSign')
            has_crl_sign = ext.get_usage('cRLSign')
            if has_crl_sign or has_cert_sign:
                if not ca_requested:
                    raise v_errors.ValidationError(
                        "Key usage doesn't match requested CA status "
                        "(keyCertSign/cRLSign: %s/%s)"
                        % (has_cert_sign, has_crl_sign))
                request_ca_flags = True
    if ca_requested and not request_ca_flags:
        raise v_errors.ValidationError("CA flags required")


def source_cidrs(request=None, cidrs=None, **kwargs):
    """Ensure that the request comes from a known source."""
    for cidr in cidrs:
        try:
            r = netaddr.IPNetwork(cidr)
            if request.client_addr in r:
                return
        except netaddr.AddrFormatError:
            raise v_errors.ValidationError(
                "Cidr '%s' does not describe a valid network" % cidr)
    raise v_errors.ValidationError(
        "No network matched the request source '%s'" %
        request.client_addr)


def csr_signature(csr=None, **kwargs):
    """Ensure that the CSR has a valid self-signature."""
    try:
        if not csr.verify():
            raise v_errors.ValidationError("Signature on the CSR is not valid")
    except errors.X509Error:
        raise v_errors.ValidationError("Signature on the CSR is not valid")