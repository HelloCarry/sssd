""" IPA AD Trust Sanity tests

:requirement: IDM-SSSD-REQ: Testing SSSD in IPA Provider
:casecomponent: sssd
:subsystemteam: sst_idm_sssd
:upstream: yes
:status: approved
"""

import random
import re
import time
from pexpect import pxssh
import pytest
from sssd.testlib.common.utils import sssdTools
from sssd.testlib.common.utils import ADOperations
from sssd.testlib.common.exceptions import SSHLoginException
from sssd.testlib.common.expect import pexpect_ssh


@pytest.mark.usefixtures('setup_ipa_client')
@pytest.mark.tier2
@pytest.mark.trust
class TestADTrust(object):
    """ IPA AD Trust tests """
    @staticmethod
    def test_basic_sssctl_list(multihost):
        """
        :title: Verify sssctl lists trusted domain
        :id: 8da8919d-524c-4498-8dc8-608eb5e139b0
        """
        domain_list = 'sssctl domain-list'
        ad_domain_name = multihost.ad[0].domainname
        cmd = multihost.master[0].run_command(domain_list, raiseonerr=False)
        mylist = cmd.stdout_text.split()
        assert ad_domain_name in mylist

    @staticmethod
    def test_pam_sss_gss_handle_large_krb_ticket(
            multihost, create_aduser_group):
        """
        :title: Verify pam_sss_gss.so can handle large kerberos ticket
                for sudo
        :id: 456ea53b-6702-4b8e-beb1-eee841b85fed
        :bugzilla: https://bugzilla.redhat.com/show_bug.cgi?id=1948657
        :steps:
         1. Add sudo rule in IPA-server for AD-users
         2. Modify /etc/krb5.conf.d/kcm_default_ccache to specify location
            of storing a TGT
         3. Enable pam_sss_gss.so for auth in /etc/pam.d/{sudo,sudo-i} files
         4. Add a sudo rule for AD-user
         5. Log in on ipa-client as AD-user
         6. Run kinit and fetch tgt
         7. Run sudo command
         8. Remove sudo cache
         9. Run sudo command again
        :expectedresults:
         1. Should succeed
         2. Should succeed
         3. Should succeed
         4. Should succeed
         5. Should succeed
         6. Should succeed
         7. Should not ask password, and should succeed
         8. Should succeed
         9. Should not ask password, and should succeed

        """
        # pylint: disable=too-many-locals, too-many-statements
        (aduser, _) = create_aduser_group
        ad_dmn_name = multihost.ad[0].domainname
        fq_aduser = f'{aduser}@{ad_dmn_name}'
        client = sssdTools(multihost.client[0], multihost.ad[0])
        ipaserver = sssdTools(multihost.master[0])
        cmd = 'dnf install -y sssd sssd-kcm'
        multihost.client[0].run_command(cmd, raiseonerr=False)
        domain_name = ipaserver.get_domain_section_name()
        domain_section = 'domain/{}'.format(domain_name)
        params = {'pam_gssapi_services': 'sudo, sudo-i'}
        client.sssd_conf(domain_section, params)
        krbkcm = '/etc/krb5.conf.d/kcm_default_ccache'
        bk_krbkcm = '/tmp/kcm_default_ccache'
        multihost.client[0].run_command(f'cp {krbkcm} {bk_krbkcm}')
        cmd = "echo -e  '[libdefaults]\n' \
              '    default_ccache_name  = FILE:/tmp/krb5cc_%{uid}:'"
        multihost.client[0].run_command(cmd, raiseonerr=False)
        multihost.client[0].service_sssd('restart')
        pam_sss_gss = "auth       sufficient   pam_sss_gss.so debug"
        for pam_file in "/etc/pam.d/sudo-i", "/etc/pam.d/sudo":
            cmd = f'sed -i "1 i {pam_sss_gss}" {pam_file}'
            multihost.client[0].run_command(cmd, raiseonerr=False)
        cmd = f'echo "{fq_aduser} ALL=(ALL) ALL" >> /etc/sudoers'
        multihost.client[0].run_command(cmd, raiseonerr=False)
        log = re.compile('.*System.*error.*Broken.*pipe.*')

        p_ssh = pxssh.pxssh(
            options={"StrictHostKeyChecking": "no",
                     "UserKnownHostsFile": "/dev/null"}
        )
        p_ssh.force_password = True
        try:
            p_ssh.login(multihost.client[0].ip, fq_aduser, 'Secret123')
            p_ssh.sendline(f'kinit {fq_aduser}')
            p_ssh.expect('Password for .*:', timeout=10)
            p_ssh.sendline('Secret123')
            p_ssh.prompt(timeout=5)
            p_ssh.sendline('sudo -l')
            p_ssh.prompt(timeout=5)
            sudo_out_1 = str(p_ssh.before)
            p_ssh.sendline('sudo id; echo "retcode:$?"')
            p_ssh.prompt(timeout=5)
            sudo_out_2 = str(p_ssh.before)
            p_ssh.sendline('sudo -l; echo "retcode:$?"')
            p_ssh.prompt(timeout=5)
            sudo_out_3 = str(p_ssh.before)
            p_ssh.logout()
        except pxssh.ExceptionPxssh:
            pytest.fail("Failed to login via ssh.")

        result = True
        for line in sudo_out_1.splitlines():
            res = log.search(line)
            result = result and res is None
        client.sssd_conf(domain_section, params, action='delete')
        for pam_file in "/etc/pam.d/sudo-i", "/etc/pam.d/sudo":
            cmd = f'sed -i "1d" {pam_file}'
            multihost.client[0].run_command(cmd, raiseonerr=False)
        cmd = 'sed -i "$ d" /etc/sudoers'
        multihost.client[0].run_command(cmd, raiseonerr=False)
        cmd = f'mv {bk_krbkcm} {krbkcm}'
        multihost.client[0].run_command(cmd, raiseonerr=False)
        assert result is True, "Error occurred with large ticket!"
        assert "retcode:0" in sudo_out_2, "sudo id failed"
        assert "retcode:0" in sudo_out_3, "sudo -l failed"

    @staticmethod
    def test_adgrpwith_at_ratesign(multihost):
        """
        :title: user membership of AD group with @ sign
        :id: ee9ca809-6ea7-48f2-a0fe-d9eccadf5d81
        :customerscenario: True
        :bugzilla:
         https://bugzilla.redhat.com/show_bug.cgi?id=2061795
        :description:
         In the IPA-AD trust, IPA-user having group membership of an
         AD-group, containing '@' sign in it's name, should be resolvable
         with default re_expression on a ipa-client system.
        :steps:
          1. Create an AD-group having '@' sign in it's name
          2. Create an AD-user and add it to above created group
          3. From ipaclient, ad-group with '@' sign is correctly fetched
          4. From ipaclient, confirm ad-user is showing correct group
             membership
        :expectedresults:
          1. Should succeed
          2. Should succeed
          3. Should succeed
          4. Should succeed
        """
        client = sssdTools(multihost.client[0], multihost.ad[0])
        ad = ADOperations(multihost.ad[0])
        ad_dmn = multihost.ad[0].domainname
        ad_user = 'aduser7'
        ad_group = 'adgrp@7'
        ad.create_ad_unix_user_group(ad_user, ad_group)
        client.clear_sssd_cache()
        cmd = multihost.client[0].run_command(
            f'getent group {ad_group}@{ad_dmn}', raiseonerr=False)
        cmd1 = multihost.client[0].run_command(
            f'id {ad_user}@{ad_dmn}', raiseonerr=False)
        ad.delete_ad_user_group(ad_user)
        ad.delete_ad_user_group(ad_group)
        assert ad_group in cmd.stdout_text,\
            f"{ad_group} information is fetched correctly"
        assert ad_group in cmd1.stdout_text,\
            f"{ad_group} is available in {ad_user} information"

    @staticmethod
    def test_ipaserver_sss_cache_user(multihost):
        """
        :title: Verify AD user is cached on IPA server
         when ipa client queries AD User
        :id: 4a48ee7a-62d1-4eea-9f33-7df3fccc908e
        """
        ipaserver = sssdTools(multihost.master[0])
        domain_name = ipaserver.get_domain_section_name()
        cache_path = '/var/lib/sss/db/cache_%s.ldb' % domain_name
        ad_domain_name = multihost.ad[0].domainname
        user_name = 'Administrator@%s' % ad_domain_name
        id_cmd = 'id %s' % user_name
        multihost.master[0].run_command(id_cmd, raiseonerr=False)
        multihost.client[0].run_command(id_cmd, raiseonerr=False)
        d_n = f'name=Administrator@{ad_domain_name},cn=users,' \
              f'cn={ad_domain_name},cn=sysdb'
        ldb_cmd = 'ldbsearch -H %s -b "%s"' % (cache_path, d_n)
        multihost.master[0].run_command(ldb_cmd, raiseonerr=False)

    @staticmethod
    def test_enforce_gid(multihost):
        """
        :title: Verify whether the new gid is enforceable when
         gid of AD Group Domain Users is overridden
        :id: 3581c7c0-d598-4e34-bb9b-9d791b93ec65
        :bugzilla:
         https://bugzilla.redhat.com/show_bug.cgi?id=1817219
        """
        create_view = 'ipa idview-add  foo_bar'
        multihost.master[0].run_command(create_view)
        ad_domain_name = multihost.ad[0].domainname
        ad_grp = 'Domain Users@%s' % ad_domain_name
        cmd = 'ipa idoverridegroup-add foo_bar "%s" --gid=40000000' % ad_grp
        multihost.master[0].run_command(cmd, raiseonerr=False)
        # apply the view on client
        client_hostname = multihost.client[0].sys_hostname
        apply_view = "ipa idview-apply foo_bar --hosts=%s" % client_hostname
        multihost.master[0].run_command(apply_view)
        client = sssdTools(multihost.client[0])
        client.clear_sssd_cache()
        time.sleep(5)
        user_name = 'Administrator@%s' % ad_domain_name
        id_cmd = 'id %s' % user_name
        cmd = multihost.client[0].run_command(id_cmd, raiseonerr=False)
        group = "40000000(domain users@%s)" % ad_domain_name
        delete_id_view = 'ipa idview-del foo_bar'
        multihost.master[0].run_command(delete_id_view)
        client.clear_sssd_cache()
        assert group in cmd.stdout_text

    @staticmethod
    def test_honour_idoverride(multihost, create_aduser_group):
        """
        :title: Verify sssd honours the customized ID View
        :id: 0c0dcfbb-6099-4c61-81c9-3bd3a003ff58
        :bugzilla:
         https://bugzilla.redhat.com/show_bug.cgi?id=1826720
        """
        (aduser, _) = create_aduser_group
        domain = multihost.ad[0].domainname
        ipa_client = sssdTools(multihost.client[0])
        ipa_client.clear_sssd_cache()
        ad_user_fqdn = '%s@%s' % (aduser, domain)
        id_cmd = 'id -g %s' % ad_user_fqdn
        cmd = multihost.master[0].run_command(id_cmd, raiseonerr=False)
        current_gid = cmd.stdout_text.strip()
        create_view = 'ipa idview-add madrid_trust_view'
        multihost.master[0].run_command(create_view)
        cmd = 'ipa idoverrideuser-add madrid_trust_view '\
              '%s --uid=50001 --gidnumber=50000 '\
              '--home=/home/%s' % (ad_user_fqdn, aduser)
        multihost.master[0].run_command(cmd, raiseonerr=False)
        # apply the view on client
        apply_view = "ipa idview-apply madrid_trust_view "\
                     "--hosts=%s" % multihost.client[0].sys_hostname
        multihost.master[0].run_command(apply_view)
        ipa_client.clear_sssd_cache()
        time.sleep(5)
        id_cmd = 'id %s' % ad_user_fqdn
        count = 0
        for _ in range(50):
            cmd = multihost.client[0].run_command(id_cmd, raiseonerr=False)
            gid = cmd.stdout_text.strip()
            if gid == current_gid:
                count += 1
        delete_id_view = 'ipa idview-del madrid_trust_view'
        multihost.master[0].run_command(delete_id_view)
        ipa_client.clear_sssd_cache()
        assert count == 0

    @staticmethod
    def test_ipa_missing_secondary_ipa_posix_groups(multihost,
                                                    create_aduser_group):
        """
        :title: IPA missing secondary IPA Posix groups in latest sssd
        :id: bbb82516-4127-4053-9b06-9104ac889819
        :setup:
         1. Configure trust between IPA server and AD.
         2. Configure client machine with SSSD integrated to IPA.
         3. domain-resolution-order set so the AD domains are checked first
         4. Create external group that is member of a posix group
         5. Create user that is a member of the external group
         6. Make sure that external group is member of posix group.
        :steps:
         0. Clean sssd cache
         1. Run getent group for posix group and using id check that user
            is member of posix group.
        :expectedresults:
         0. Cache is cleared.
         1. The posix group gid is present in id output.
        :teardown:
         Remove the created user, groups and revert resolution order.
        :customerscenario: True
        :bugzilla:
         https://bugzilla.redhat.com/show_bug.cgi?id=1945552
         https://bugzilla.redhat.com/show_bug.cgi?id=1937919
         https://bugzilla.redhat.com/show_bug.cgi?id=1945654
        """
        # pylint: disable=too-many-locals
        ad_domain = multihost.ad[0].domainname
        ipaserver = sssdTools(multihost.master[0])
        ipa_domain = ipaserver.get_domain_section_name()
        (username, _) = create_aduser_group
        posix_group = "posix_group_01"
        ext_group = "ext_group_01"
        # SETUP
        # Set the domain resolution order to AD first
        resorder_cmd = f'ipa config-mod --domain-resolution-order=' \
                       f'{ad_domain}:{ipa_domain}'
        multihost.master[0].run_command(resorder_cmd, raiseonerr=False)

        # Create posix group
        pgroup_cmd = f'ipa group-add {posix_group}'
        multihost.master[0].run_command(pgroup_cmd, raiseonerr=False)

        # Create and external group
        ext_group_cmd = f'ipa group-add --external {ext_group}'
        multihost.master[0].run_command(ext_group_cmd, raiseonerr=False)

        # Set membership of external group in posix group
        member_cmd = f'ipa -n group-add-member {posix_group} --groups=' \
                     f'{ext_group}'
        multihost.master[0].run_command(member_cmd, raiseonerr=False)

        # Set AD user membership in external group
        usr_mbr_cmd = f"ipa -n group-add-member {ext_group} --external" \
                      f" '{username}@{ad_domain}'"
        multihost.master[0].run_command(usr_mbr_cmd, raiseonerr=False)

        # TEST
        # Get posix group id
        grp_show_cmd = f"ipa group-show {posix_group}"
        cmd = multihost.master[0].run_command(grp_show_cmd, raiseonerr=False)
        gid_regex = re.compile(r"GID: (\d+)")
        posix_group_id = gid_regex.search(cmd.stdout_text).group(1)

        # Check that external group is member of posix group
        grp_show_cmd = f"ipa group-show {ext_group}"
        cmd = multihost.master[0].run_command(grp_show_cmd, raiseonerr=False)
        assert posix_group in cmd.stdout_text, \
            "The external group is not a member of posix group!"

        # A bit of wait so the user is propagated
        time.sleep(60)

        # The reproduction rate is not 100%, I had reliably 2+
        # fails in 5 rounds.
        for _ in range(5):
            # Clean caches on SSSD so we don't have to wait for cache timeouts
            # The reproduction works better on sssd on ipa master
            sssd_client = sssdTools(multihost.master[0])
            sssd_client.clear_sssd_cache()

            # Search the posix group using getent to trigger the condition with
            # negative cache
            getent_cmd = f"getent group {posix_group_id}"
            multihost.master[0].run_command(getent_cmd, raiseonerr=False)

            # Check that posix group is listed in id
            id_cmd = f"id {username}@{ad_domain}"
            cmd = multihost.master[0].run_command(id_cmd, raiseonerr=False)
            # Check if id worked
            assert cmd.returncode == 0,\
                'Could not find the user, something wrong with setup!'
            # Check if the posix group was found for the user.
            assert posix_group_id in cmd.stdout_text,\
                "The user is not a member of posix group!"

        # TEARDOWN
        # Remove user from external group
        usr_mbr_del_cmd = f"ipa -n group-remove-member {ext_group} " \
                          f"--external '{username}@{ad_domain}'"
        multihost.master[0].run_command(usr_mbr_del_cmd, raiseonerr=False)

        # Remove group membership
        grp_del_mbr_cmd = f'ipa -n group-remove-member {posix_group}' \
                          f' --groups={ext_group}'
        multihost.master[0].run_command(grp_del_mbr_cmd, raiseonerr=False)

        # Remove external group
        ext_grp_del_cmd = f'ipa group-del {ext_group}'
        multihost.master[0].run_command(ext_grp_del_cmd, raiseonerr=False)

        # Remove posix group
        px_grp_del_cmd = f'ipa group-del {posix_group}'
        multihost.master[0].run_command(px_grp_del_cmd, raiseonerr=False)

        # Reset the domain resolution order
        rev_resorder_cmd = f'ipa config-mod --domain-resolution-order=' \
                           f'{ipa_domain}:{ad_domain}'
        multihost.master[0].run_command(rev_resorder_cmd, raiseonerr=False)

    @staticmethod
    def test_nss_get_by_name_with_private_group(multihost):
        """
        :title:
         SSSD fails nss_getby_name for IPA user with SID if the user has
         a private group
        :id: 45dce6b9-0d47-4b9f-9532-4da8178e5334
        :setup:
         1. Configure trust between IPA server and AD.
         2. Configure client machine with SSSD integrated to IPA.
         3. Create an user with a private group
        :steps:
         1. Call function getsidbyname from pysss_nss_idmap for admin.
         2. Call function getsidbyname from pysss_nss_idmap for then user.
        :expectedresults:
         1. The admin SID is returned.
         2. The user SID is returned.
        :teardown:
         Remove the created user.
        :bugzilla:
         https://bugzilla.redhat.com/show_bug.cgi?id=1837090
        """
        # Create an user with a private group
        username = 'some-user'
        multihost.master[0].run_command(
            f'ipa user-add {username} --first=Some --last=User',
            raiseonerr=False
        )

        # Confirm that the user exists
        cmd = multihost.master[0].run_command(
            f'id  {username}',
            raiseonerr=False
        )
        # First check for admin user to make sure that the setup is correct
        check_admin_cmd = '''python3 -c "import pysss_nss_idmap; import '''\
            '''sys; result=pysss_nss_idmap.getsidbyname('admin');'''\
            '''print(result); result or sys.exit(2)"'''
        cmd_adm = multihost.master[0].run_command(check_admin_cmd,
                                                  raiseonerr=False)

        # Now check for the user with the private group
        check_user_cmd = '''python3 -c "import pysss_nss_idmap; import sys;'''\
            '''result=pysss_nss_idmap.getsidbyname('%s');print(result); '''\
            '''result or sys.exit(2)"''' % username
        cmd_usr = multihost.master[0].run_command(check_user_cmd,
                                                  raiseonerr=False)

        # Remove the user afterwards
        user_del_cmd = f'ipa user-del {username}'
        multihost.master[0].run_command(user_del_cmd, raiseonerr=False)

        # Evaluate results after cleanup is done
        assert cmd.returncode == 0, 'Could not find the user!'
        assert cmd_adm.returncode == 0, 'Something wrong with setup!'
        assert cmd_usr.returncode == 0, \
            f"pysss_nss_idmap.getsidbyname for {username} failed"

    @staticmethod
    def test_idview_override_group_fails(multihost, create_aduser_group):
        """
        :title: IPA clients fail to resolve override group names in custom view
        :id: 7a0dc871-fdad-4c07-9d07-a092baa83178
        :customerscenario: true
        :bugzilla:
          https://bugzilla.redhat.com/show_bug.cgi?id=2004406
          https://bugzilla.redhat.com/show_bug.cgi?id=2031729
        :description: Overriding both user and group names and ids in
          an idview for user and group from AD results in error in sssd
          when running id command.
        :setup:
          1. Create user and group (group1) on AD.
          2. Make AD user member of group1.
          3. Create additional group (group2) on AD.
        :steps:
          1. ID views to override AD groupname and gid of group1.
          2. ID views to override AD groupname and gid of group2.
          3. ID view to override AD username, uid and gid (to gid of group2).
          4. Run an "id" command for the override user.
        :expectedresults:
          1. View with an override is created.
          2. View with an override is created.
          3. User override is added to the view.
          4. Id command succeeds, group override is visible, all groups are
             properly resolved.
        """
        (aduser, adgroup) = create_aduser_group
        run_id_int = random.randint(9999, 999999)
        adgroup2 = f"group2_{run_id_int}"
        ado = ADOperations(multihost.ad[0])
        ado.create_ad_unix_group(adgroup2)
        domain = multihost.ad[0].domainname

        ipa_client = sssdTools(multihost.client[0])
        ipa_client.clear_sssd_cache()

        view = f'prygl_trust_view_{run_id_int}'
        create_view = f'ipa idview-add {view}'
        multihost.master[0].run_command(create_view, raiseonerr=False)

        create_grp_override = f'ipa idoverridegroup-add "{view}" ' \
            f'{adgroup}@{domain} --group-name ' \
            f'"borci{run_id_int}" --gid={run_id_int+1}'
        multihost.master[0].run_command(create_grp_override, raiseonerr=False)

        create_grp2_override = f'ipa idoverridegroup-add "{view}" ' \
            f'{adgroup2}@{domain} --group-name ' \
            f'"magori{run_id_int}" --gid={run_id_int+2}'
        multihost.master[0].run_command(create_grp2_override, raiseonerr=False)

        create_user_override = f'ipa idoverrideuser-add "{view}" ' \
            f'{aduser}@{domain} --login ferko{run_id_int} ' \
            f'--uid=50001 --gidnumber={run_id_int+2}'
        multihost.master[0].run_command(create_user_override, raiseonerr=False)

        # Apply the view on client
        multihost.master[0].run_command(
            f"ipa idview-apply '{view}' --hosts="
            f"{multihost.client[0].sys_hostname}", raiseonerr=False)

        ipa_client.clear_sssd_cache()
        time.sleep(5)
        cmd = multihost.client[0].run_command(
            f'id ferko{run_id_int}@{domain}', raiseonerr=False)

        # TEARDOWN
        ado.delete_ad_user_group(adgroup2)
        multihost.master[0].run_command(
            f'ipa idview-del {view}', raiseonerr=False)

        # Test result Evaluation
        assert cmd.returncode == 0, f"User {aduser} was not found."
        assert f"borci{run_id_int}@{domain}" in cmd.stdout_text,\
            f"Group 1 {adgroup} name was not overridden/resolved."
        assert f"magori{run_id_int}@{domain}" in cmd.stdout_text,\
            f"Group 2 {adgroup2} name was not overridden/resolved."
        assert f"{run_id_int+1}" in cmd.stdout_text,\
            "Group 1 id was not overridden."
        assert f"{run_id_int+2}" in cmd.stdout_text,\
            "Group 2 id was not overridden."
        assert f"domain users@{domain}" in cmd.stdout_text, \
            "Group domain users is missing."

    @staticmethod
    def test_ad_user_ssh_ipa_client(multihost):
        """
        :title: Cannot SSH with AD user to ipa-client
        :description: 'krb5_validate' and 'pac_check' settings conflicted
          before the fix. By default 'krb5_validate = true' with id_providers
          ipa and ad_provider. Setting it to false will enable login even if
          the ticket validation does not work
        :id: d4af4084-0ee2-4339-be66-ef117439f32d
        :bugzilla: https://bugzilla.redhat.com/show_bug.cgi?id=2128544
                   https://bugzilla.redhat.com/show_bug.cgi?id=2128902
        :steps:
          1. Add an invalid entry keytab after taking a backup
          2. ssh as AD user on the ipa client machine
          3. Set 'krb5_validate = false', debug_level 9 in sssd.conf,
             and restart sssd service
          4. ssh as AD user on the ipa client machine
          5. Check for log message on PAC check being skipped with
             krb_validate set to false
        :expectedresults:
          1. Invalid entry is added at the end of the keytab
          2. AD user ssh login fails
          3. sssd restart successfully with 'krb5_validate = false'
          4. AD user ssh login is successful with the setting
          5. Log message about skipping pac_check is found
        """
        ad_domain_name = multihost.ad[0].domainname
        client_hostip = multihost.client[0].ip
        multihost.client[0].run_command('cp -f /etc/krb5.keytab '
                                        '/etc/krb5.keytab.bak', raiseonerr=False)
        ktutil_cmd = '(echo "rkt /etc/krb5.keytab"; sleep 1; echo "addent ' \
                     '-password -p invalid@invaliddom -k 3 -e ' \
                     'aes128-cts-hmac-sha1-96"; sleep 1; echo "pass00189";' \
                     'sleep 1; echo "list"; sleep 1; echo ' \
                     '"wkt /tmp/invalid.keytab"; sleep 1; echo "quit";) | ktutil'

        multihost.client[0].run_command(ktutil_cmd, raiseonerr=False)
        multihost.client[0].run_command('mv /tmp/invalid.keytab '
                                        '/etc/krb5.keytab', raiseonerr=False)
        multihost.client[0].run_command('restorecon /etc/krb5.keytab', raiseonerr=False)
        multihost.client[0].run_command('klist -ekt /etc/krb5.keytab', raiseonerr=False)

        client = pexpect_ssh(client_hostip, f'user1@{ad_domain_name}',
                             'Secret123', debug=True)
        with pytest.raises(Exception):
            client.login(login_timeout=10, sync_multiplier=1,
                         auto_prompt_reset=False)

        tools = sssdTools(multihost.client[0])
        domain_params = {'debug_level': '9', 'krb5_validate': 'false'}
        ipaserver = sssdTools(multihost.master[0])
        domain_name = ipaserver.get_domain_section_name()
        tools.sssd_conf(f'domain/{domain_name}', domain_params)
        tools.clear_sssd_cache()

        client = pexpect_ssh(client_hostip, f'user1@{ad_domain_name}',
                             'Secret123', debug=True)
        try:
            client.login(login_timeout=30, sync_multiplier=2,
                         auto_prompt_reset=False)
        except SSHLoginException:
            pytest.fail("user failed to login")
        else:
            client.logout()

        multihost.client[0].run_command('cp -f /etc/krb5.keytab.bak '
                                        '/etc/krb5.keytab', raiseonerr=False)
        multihost.client[0].run_command('restorecon /etc/krb5.keytab', raiseonerr=False)

        log_str = 'PAC check is requested but krb5_validate is '\
                  'set to false. PAC checks will be skipped'

        multihost.client[0].run_command(f'grep -i "{log_str}" '
                                        '/var/log/sssd/krb5_child.log')
        multihost.client[0].run_command(f'grep -i "{log_str}" '
                                        f'/var/log/sssd/sssd_{domain_name}.log')
