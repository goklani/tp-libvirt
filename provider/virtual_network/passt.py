import logging
from socket import socket

import aexpect
from avocado.core import exceptions
from avocado.utils import process
from virttest import utils_misc
from virttest import utils_net

VIRSH_ARGS = {'ignore_status': False, 'debug': True}
IPV6_LENGTH = 128

LOG = logging.getLogger('avocado.' + __name__)


def get_user_id(user):
    """
    Get user id

    :param user: user name
    :return: id of user, str type
    """
    return process.run(f'id -u {user}').stdout_text.strip()


def get_free_port():
    """
    Get a random free port

    :return: port id
    """
    with socket() as s:
        s.bind(('', 0))
        return s.getsockname()[1]


def get_iface_ip_and_prefix(iface, session=None, ip_ver='ipv4'):
    """
    Get ip and prefix of interface

    :param iface: inteface name
    :param session: shell session instance if any, defaults to None
    :param ip_ver: ip version, defaults to 'ipv4'
    :return: list of tuples of ip address and ip prefixlen
    """
    iface_info = utils_net.get_linux_iface_info(iface=iface, session=session)

    if ip_ver == 'ipv4':
        return [(addr['local'], addr['prefixlen'])
                for addr in iface_info['addr_info']
                if addr['family'] == 'inet'][0]
    if ip_ver == 'ipv6':
        return [(addr['local'], addr['prefixlen'])
                for addr in iface_info['addr_info']
                if addr['family'] == 'inet6' and addr['scope'] == 'global']


def check_proc_info(params, log_file, mac):
    """
    Check process info of passt

    :param params: test params
    :param log_file: path of log file
    :param mac: mac address
    """
    socket_dir = params.get('socket_dir')

    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_net_if(
        state="UP")[0]

    #  check the passt process cmd line
    pid_passt = process.run('pidof passt').stdout_text.strip()
    passt_proc = process.run(f'ps -fp {pid_passt} -Z', shell=True).stdout_text
    title, content = passt_proc.strip().split('\n')
    title = title.split()
    content = content.split(maxsplit=len(title) - 1)
    proc_info = dict(zip(title, content))
    LOG.debug(proc_info)

    # check the lable is passt_t
    if 'passt_t' not in proc_info['LABEL']:
        raise exceptions.TestFail('"passt_t" should be in process label')

    # check the mac, interface name, log file path is consistent with the xml;
    failed_check = []

    def check_proc(target):
        if target in proc_info['CMD']:
            LOG.debug(f'Check "{target}" in CMD PASSED')
        else:
            failed_check.append(f'{target} should be in process CMD')

    check_list = [
        f'--mac-addr {mac}',
        f'--interface {host_iface}',
        f'--log-file {log_file}',
        f'--socket {socket_dir}',
        f'--pid {socket_dir}',
    ]
    if params.get('proc_checks'):
        check_list.extend(params['proc_checks'])

    list(map(check_proc, check_list))
    if failed_check:
        raise exceptions.TestFail(';'.join(failed_check))


def check_vm_ip(iface_attrs, session, host_iface):
    """
    Check if vm ip and prefix meet expectation

    :param iface_attrs: attributes of interface
    :param session: shell session instance of vm
    :param host_iface: host interface
    """
    vm_ip, prefix = get_iface_ip_and_prefix('eno1', session=session)
    LOG.debug(f'VM ip and prefix: {vm_ip}, {prefix}')
    if 'ips' in iface_attrs:
        vm_ip_info = [ip for ip in iface_attrs['ips']
                      if ip['family'] == 'ipv4'][0]
        if (vm_ip, prefix) != (vm_ip_info['address'],
                               int(vm_ip_info['prefix'])):
            raise exceptions.TestFail(f'Wrong vm address and prefix: {vm_ip}, '
                                      f'{prefix},'
                                      f'Should be {vm_ip_info["address"]}, '
                                      f'{vm_ip_info["prefix"]}')

    else:
        host_ip, prefix = get_iface_ip_and_prefix(host_iface)
        LOG.debug(f'Host ip and prefix: {host_ip}, {prefix}')
        if (vm_ip, prefix) != (host_ip, prefix):
            raise exceptions.TestFail('vm ip and prefix should be '
                                      'the same as host')

    set_ipv6_info = get_iface_ip_and_prefix('eno1', ip_ver='ipv6')[0]
    if 'ips' in iface_attrs:
        iface_ipv6_info = [ip for ip in iface_attrs['ips']
                           if ip['family'] == 'ipv6'][0]
        set_ipv6_info = iface_ipv6_info['address'], set_ipv6_info[1]

    LOG.debug(f'Host ipv6 addr and prefix: {set_ipv6_info}')
    vm_ipv6_info = get_iface_ip_and_prefix('eno1', session, ip_ver='ipv6')
    LOG.debug(f'VM ipv6 addr and prefix: {vm_ipv6_info}')
    vm_ipv6_dhcp = set_ipv6_info[0], IPV6_LENGTH
    if vm_ipv6_dhcp not in vm_ipv6_info:
        raise exceptions.TestFail(f'{vm_ipv6_dhcp} should be in vm ipv6 info')
    vm_ipv6_info.remove(vm_ipv6_dhcp)
    for ip_info in vm_ipv6_info:
        if ip_info[1] != set_ipv6_info[1]:
            raise exceptions.TestFail(f'Prefix should be {set_ipv6_info[1]}, '
                                      f'not {ip_info[1]}')


def check_vm_mtu(session, iface, mtu):
    """
    Check whether mtu of vm meets expectation

    :param session: vm shell session instance
    :param iface: iface to be checked
    :param mtu: expected mtu size
    """
    vm_iface_info = utils_net.get_linux_iface_info(iface=iface, session=session)
    vm_mtu = vm_iface_info['mtu']
    if int(vm_mtu) != int(mtu):
        raise exceptions.TestFail(f'Wrong vm mtu: {vm_mtu}, should be {mtu}')


def check_default_gw(session):
    """
    Check whether default host gateways of host and guest are consistent

    :param session: vm shell session instance
    """
    host_gw = utils_net.get_default_gateway()
    vm_gw = utils_net.get_default_gateway(session=session)
    LOG.debug(f'Host and vm default ipv4 gateway: {host_gw}, {vm_gw}')
    host_gw_v6 = utils_net.get_default_gateway(ip_ver='ipv6')
    vm_gw_v6 = utils_net.get_default_gateway(session=session, ip_ver='ipv6')
    LOG.debug(f'Host and vm default ipv6 gateway: {host_gw_v6}, {vm_gw_v6}')

    if host_gw != vm_gw:
        raise exceptions.TestFail(
            'Host default ipv4 gateway not consistent with vm.')
    if host_gw_v6 != vm_gw_v6:
        raise exceptions.TestFail(
            'Host default ipv6 gateway not consistent with vm.')


def check_nameserver(session):
    """
    Check whether nameserver of host and vm are consistent

    :param session: vm shell session instance
    """
    get_cmd = 'cat /etc/resolv.conf'
    on_host = process.run(get_cmd).stdout_text.strip()
    on_vm = session.cmd_output(get_cmd).strip()
    if on_host == on_vm:
        LOG.debug(f'Nameserver on vm is consistent with host:\n{on_host}')
    else:
        LOG.debug(
            f'Nameserver on host:\n{on_host}\nNameserver on vm:\n{on_vm}')
        raise exceptions.TestFail(
            'Nameserver on vm is not consistent with host')


def check_protocol_connection(src_sess, tar_sess, protocol, addr,
                              src_iface=None, src_port=None, tar_port=None,
                              expected=True, expect_msg=None):
    """
    Check connetion of host and vm with 'socat' command

    :param src_sess: shell session instance of source server
    :param tar_sess: shell session instance of target server
    :param protocol: network protocol
    :param addr: address to connect
    :param src_iface: interface of source, defaults to None
    :param src_port: port of source, defaults to None
    :param tar_port: port of target, defaults to None
    :param expected: expected success, defaults to True
    :param expect_msg: expected message, defaults to None
    """
    if src_port is None or tar_port is None:
        src_port = tar_port = get_free_port()
    info = f'Connectivity of {protocol} from source:{src_port} to '\
           f'target:{tar_port}'
    LOG.info(f'Check {info}')
    cmd_listen = f'socat {protocol}-LISTEN:{tar_port} -'
    LOG.debug(cmd_listen)
    tar_sess.sendline(cmd_listen)
    msg = f'Message {utils_misc.generate_random_string(6)} '\
          f'from source via {protocol}'
    if src_iface:
        addr = f'{addr}%{src_iface}'
    if ':' in addr:
        addr = f'[{addr}]'
    cmd = f'echo "{msg}" | socat -u STDIN {protocol}:{addr}:{src_port}'

    src_output = src_sess.cmd_output(cmd)
    LOG.debug(f'Output of source:\n{src_output}')
    if expect_msg:
        if expect_msg in src_output:
            LOG.debug(f'Found expect msg {expect_msg}')
        else:
            raise exceptions.TestFail(f'Not found expect msg {expect_msg}')
    tar_sess.sendcontrol('c')
    tar_output = '\n'.join(tar_sess.get_output().splitlines()[-5:])
    LOG.debug(tar_output)
    conneted = msg in tar_output
    LOG.debug(f'{info} is {conneted}')
    if conneted == expected:
        LOG.info(f'Check {info}: PASS')
    else:
        raise exceptions.TestFail(f'Check {info}: FAIL, expected {expected}, '
                                  f'actually is {conneted}')


def check_connection(vm, vm_iface, protocols):
    """
    Check connection of vm and host

    :param vm: vm instance
    :param vm_iface: interface of vm
    :param protocols: protocols to check
    """
    default_gw = utils_net.get_default_gateway()
    default_gw_v6 = utils_net.get_default_gateway(ip_ver='ipv6')
    for protocol in protocols:
        host_sess = aexpect.ShellSession('su')
        vm_sess = vm.wait_for_serial_login(timeout=60)
        gw = default_gw if protocol[-1] == '4' else default_gw_v6
        src_iface = None if protocol[-1] == '4' else vm_iface
        try:
            check_protocol_connection(vm_sess, host_sess, protocol, gw,
                                      src_iface=src_iface)
        finally:
            host_sess.close()
            vm_sess.close()


def check_portforward_connetion(vm, check_list, test_user=None):
    """
    Check portforward connection of vm and host

    :param vm: vm instance
    :param check_list: list of args to check
    :param test_user: unprivileged user if any, defaults to None
    """
    vm_sess = vm.wait_for_serial_login(timeout=60)
    vm_sess.cmd_output('systemctl stop firewalld')
    vm_sess.close()
    cmd_create_sess = f'su - {test_user}' if test_user else 'su'

    for args in check_list:
        vm_sess = vm.wait_for_serial_login(timeout=60)
        host_sess = aexpect.ShellSession(cmd_create_sess)

        try:
            check_protocol_connection(host_sess, vm_sess, args[0], args[1],
                                      src_port=args[2], tar_port=args[3],
                                      expected=args[4], expect_msg=args[5])
        finally:
            vm_sess.close()
            host_sess.close()


def check_port_listen(ports, protocol, host_ip=None):
    """
    Check port listening status

    :param ports: ports to check
    :param protocol: protocol to check
    :param host_ip: host ip address if needed, defaults to None
    """
    if protocol.lower() not in ('tcp', 'udp'):
        raise exceptions.TestError(f'Unsupported protocol: {protocol}')
    cmd_listen = process.run(f"ss -{protocol[0].lower()}lpn|egrep 'passt.avx2'",
                             shell=True).stdout_text
    for item in ports:
        if item in cmd_listen:
            LOG.debug(f'Address:Port {item} is being listened to')
        else:
            raise exceptions.TestFail(f'Address:Port {item} is NOT being '
                                      'listened to')
