import logging as log

from avocado.utils import process

from virttest import remote
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh set-user-password

    The command set the user password inside the domain
    1. Prepare test environment, start vm with guest agent
    2. Perform virsh set-user-password operation(encrypted/ non-encrypted)
    3. Login the vm with new/old password
    4. Recover test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    encrypted = params.get("encrypted", "no") == "yes"
    option = params.get("option", "no") == "yes"
    add_user = params.get("add_user", "no") == "yes"
    set_user_name = params.get("set_user_name", "root")
    status_error = params.get("status_error", "no")
    err_domain = params.get("err_domain", "")
    err_msg = params.get("err_msg", "")
    start_ga = params.get("start_ga", "yes") == "yes"
    ori_passwd = vm.params.get("password")
    new_passwd = "a" + ori_passwd
    passwd = new_passwd

    # Back up domain XML
    vmxml_bak = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        vmxml = vmxml_bak.copy()

        if start_ga:
            # Start guest agent in vm
            vm.prepare_guest_agent(prepare_xml=False, channel=False, start=True)

        # Error test
        if status_error == "yes":
            if err_domain:
                vm_name = err_domain
            ret = virsh.set_user_password(vm_name, set_user_name, new_passwd,
                                          encrypted=encrypted, option=option, debug=True)
            libvirt.check_result(ret, err_msg)

        # Normal test
        else:
            # Get guest ip address
            session = vm.wait_for_login(timeout=30, username="root", password=ori_passwd)
            vm_ip = vm.wait_for_get_address(nic_index=0, timeout=120)

            # Add user
            if add_user:
                cmd = " rm -f /etc/gshadow.lock /etc/subgid.lock & useradd %s" % set_user_name
                status, output = session.cmd_status_output(cmd)
                if status:
                    test.error("Adding user '%s' got failed: '%s'" %
                               (set_user_name, output))
                session.close()

            # Set the user password in vm
            if encrypted:
                cmd = "openssl passwd %s" % new_passwd
                ret = process.run(cmd, shell=True)
                libvirt.check_exit_status(ret)
                en_passwd = str(ret.stdout_text.strip())
                passwd = en_passwd.replace('$', r'\$')

            ret = virsh.set_user_password(vm_name, set_user_name, passwd,
                                          encrypted=encrypted, option=option, debug=True)
            libvirt.check_exit_status(ret)

            # Login with new password
            logging.debug("Trying to log in with new password")
            try:
                session = remote.wait_for_login("ssh", vm_ip, "22", set_user_name, new_passwd,
                                                r"[\#\$]\s*$", timeout=30)
                session.close()
            except remote.LoginAuthenticationError as e:
                logging.debug(e)
                test.fail("Failed to login with new password")

    finally:
        # Recover VM
        if vm.is_alive():
            # always restore root password in case previously case execution is broken
            if status_error != "yes":
                virsh.set_user_password(vm_name, set_user_name, ori_passwd, False,
                                        option=option, debug=True)

            if start_ga:
                # Stop guest agent in vm
                vm.prepare_guest_agent(prepare_xml=False, channel=False, start=False)

            # Del user
            if add_user:
                session = vm.wait_for_login(timeout=30, username="root", password=ori_passwd)
                cmd = "userdel -f -r %s" % set_user_name
                status, output = session.cmd_status_output(cmd)
                if status:
                    test.error("Deleting user '%s' got failed: '%s'" %
                               (set_user_name, output))
                session.close()
            vm.destroy(gracefully=False)
        vmxml_bak.sync()
