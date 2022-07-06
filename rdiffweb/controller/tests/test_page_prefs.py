# -*- coding: utf-8 -*-
# rdiffweb, A web interface to rdiff-backup repositories
# Copyright (C) 2012-2021 rdiffweb contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
Created on Dec 26, 2015

@author: Patrik Dufresne
"""

from unittest.mock import MagicMock

import cherrypy

import rdiffweb.test
from rdiffweb.core.model import RepoObject, UserObject


class PrefsTest(rdiffweb.test.WebCase):

    PREFS = "/prefs/"

    login = True

    def setUp(self):
        self.listener = MagicMock()
        cherrypy.engine.subscribe('user_password_changed', self.listener.user_password_changed, priority=50)
        return super().setUp()

    def tearDown(self):
        cherrypy.engine.unsubscribe('user_password_changed', self.listener.user_password_changed)
        return super().tearDown()

    def _set_password(
        self,
        current,
        new_password,
        confirm,
    ):
        b = {
            'action': 'set_password',
            'current': current,
            'new': new_password,
            'confirm': confirm,
        }
        return self.getPage(self.PREFS, method='POST', body=b)

    def _set_profile_info(self, email):
        b = {
            'action': 'set_profile_info',
            'email': email,
        }
        return self.getPage(self.PREFS, method='POST', body=b)

    def test_change_email(self):
        self._set_profile_info("test@test.com")
        self.assertInBody("Profile updated successfully.")

    def test_change_email_with_invalid_email(self):
        self._set_profile_info("@test.com")
        self.assertInBody("Invalid email")

        self._set_profile_info("test.com")
        self.assertInBody("Invalid email")

        self._set_profile_info("test")
        self.assertInBody("Invalid email")

        self._set_profile_info("test@te_st.com")
        self.assertInBody("Invalid email")

        self._set_profile_info("test@test.com, test2@test.com")
        self.assertInBody("Invalid email")

    def test_change_password(self):
        self.listener.user_password_changed.reset_mock()
        # When udating user's password
        self._set_password(self.PASSWORD, "newpass", "newpass")
        self.assertInBody("Password updated successfully.")
        # Then a notification is raised
        self.listener.user_password_changed.assert_called_once()
        # Change it back
        self._set_password("newpass", self.PASSWORD, self.PASSWORD)
        self.assertInBody("Password updated successfully.")

    def test_change_password_with_wrong_confirmation(self):
        self._set_password(self.PASSWORD, "t", "a")
        self.assertInBody("The new password and its confirmation do not match.")

    def test_change_password_with_wrong_password(self):
        self._set_password("oups", "t", "t")
        self.assertInBody("Wrong password")

    def test_invalid_pref(self):
        """
        Check if invalid prefs url is 404 Not Found.
        """
        self.getPage("/prefs/invalid/")
        self.assertStatus(404)

    def test_update_repos(self):
        # Given a user with invalid repositories
        userobj = UserObject.get_user(self.USERNAME)
        RepoObject(userid=userobj.userid, repopath='invalid').add()
        self.assertEqual(['broker-repo', 'invalid', 'testcases'], sorted([r.name for r in userobj.repo_objs]))
        # When updating the repository list
        self.getPage(self.PREFS, method='POST', body={'action': 'update_repos'})
        self.assertStatus(200)
        # Then a success message is displayed
        self.assertInBody('Repositories successfully updated')
        # Then the list is free of inexisting repos.
        userobj.expire()
        self.assertEqual(['broker-repo', 'testcases'], sorted([r.name for r in userobj.repo_objs]))

    def test_update_notification(self):
        self.getPage("/prefs/notification/", method='POST', body={'action': 'set_notification_info', 'testcases': '7'})
        self.assertStatus(200)
        # Check database update
        repo_obj = RepoObject.query.filter(RepoObject.repopath == self.REPO).first()
        self.assertEqual(7, repo_obj.maxage)

    def test_get_page(self):
        self.getPage("/prefs/", method='GET')
        self.assertInBody("SSH")


class PrefsWithSSHKeyDisabled(rdiffweb.test.WebCase):

    default_config = {
        "disable_ssh_keys": "true",
    }

    def test_get_page(self):
        self.getPage("/prefs/", method='GET')
        self.assertNotInBody("SSH")
