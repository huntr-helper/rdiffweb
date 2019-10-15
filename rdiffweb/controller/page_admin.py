#!/usr/bin/python
# -*- coding: utf-8 -*-
# rdiffweb, A web interface to rdiff-backup repositories
# Copyright (C) 2019 rdiffweb contributors
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

from __future__ import absolute_import
from __future__ import unicode_literals

from builtins import str
import logging
import os

import cherrypy
from rdiffweb.controller import Controller, validate_isinstance
from rdiffweb.core import RdiffError, RdiffWarning
from rdiffweb.core import rdw_spider_repos
from rdiffweb.core.i18n import ugettext as _
import subprocess

# Define the logger
logger = logging.getLogger(__name__)


def get_log_files(app):
    """
    Return a list of log files to be shown in admin area.
    """
    logfiles = [app.cfg.get('logfile'), app.cfg.get('logaccessfile')]
    logfiles = [fn for fn in logfiles if fn]
    return [os.path.basename(fn) for fn in logfiles]


def get_log_data(app, logfile, num=2000):
    """
    Return a list of log files to be shown in admin area.
    """
    logfiles = [app.cfg.get('logfile'), app.cfg.get('logaccessfile')]
    logfiles = [fn for fn in logfiles if fn]
    for fn in logfiles:
        if logfile == os.path.basename(fn):
            try:
                return subprocess.check_output(['tail', '-n', str(num), fn]).decode('utf-8')
            except:
                logging.exception('fail to get log file content')
                return "Error getting file content"


class AdminPage(Controller):
    """Administration pages. Allow to manage users database."""

    def _check_user_root_dir(self, directory):
        """Raised an exception if the directory is not valid."""
        if not os.access(directory, os.F_OK) or not os.path.isdir(directory):
            raise RdiffWarning(_("User root directory %s is not accessible!") % directory)

    @cherrypy.expose
    def default(self):

        # Check if user is an administrator
        if not self.app.currentuser or not self.app.currentuser.is_admin:
            raise cherrypy.HTTPError(403)

        user_count = 0
        repo_count = 0
        for user in self.app.userdb.list():
            user_count += 1
            repo_count += len(user.repos)

        params = {"user_count": user_count,
                  "repo_count": repo_count}

        return self._compile_template("admin.html", **params)

    @cherrypy.expose
    def logs(self, filename=u""):
        
        # Check if user is an administrator
        if not self.app.currentuser or not self.app.currentuser.is_admin:
            raise cherrypy.HTTPError(403)
        
        # Check if the filename is valid.
        data = ""
        logfiles = get_log_files(self.app)
        if logfiles:
            if not filename:
                filename = logfiles[0]
                
            if filename not in logfiles:
                raise cherrypy.HTTPError(404)
            
            data = get_log_data(self.app, filename)
        
        params = {
            "filename": filename,
            "logfiles": logfiles,
            "data":  data,
        }
        return self._compile_template("admin_logs.html", **params)

    @cherrypy.expose
    def users(self, userfilter=u"", usersearch=u"", action=u"", username=u"",
              email=u"", password=u"", user_root=u"", is_admin=u""):

        # Check if user is an administrator
        if not self.app.currentuser or not self.app.currentuser.is_admin:
            raise cherrypy.HTTPError(403)

        validate_isinstance(userfilter, str)
        validate_isinstance(usersearch, str)

        # If we're just showing the initial page, just do that
        params = {}
        if self._is_submit():
            try:
                params = self._users_handle_action(action, username,
                                                   email, password, user_root,
                                                   is_admin)
            except RdiffWarning as e:
                params['warning'] = str(e)
            except RdiffError as e:
                params['error'] = str(e)

        # Get page parameters
        params.update(
            self._users_get_params_for_page(userfilter, usersearch))

        # Build users page
        return self._compile_template("admin_users.html", **params)

    def _users_get_params_for_page(self, userfilter, usersearch):
        users = [{"username": user.username,
                  "email": user.email,
                  "is_admin": user.is_admin,
                  "user_root": user.user_root,
                  } for user in self.app.userdb.list()]

        # Apply the filters.
        filtered_users = users
        if userfilter == "admins":
            filtered_users = [x for x in filtered_users if x["is_admin"]]
        # Apply the search.
        if usersearch:
            filtered_users = [x for x in filtered_users
                              if usersearch in x["username"] or
                              usersearch in x["email"]]

        return {"userfilter": userfilter,
                "usersearch": usersearch,
                "filtered_users": filtered_users,
                "users": users}

    def _users_handle_action(self, action, username, email, password,
                             user_root, is_admin):

        success = ""

        # We need to change values. Change them, then give back that main
        # page again, with a message
        if username == self.app.currentuser.username:
            # Don't allow the user to changes it's "admin" state.
            is_admin = self.app.currentuser.is_admin

        is_admin = str(is_admin).lower() in ['true', '1']

        # Fork the behaviour according to the action.
        if action == "edit":
            user = self.app.userdb.get_user(username)
            logger.info("updating user [%s] info", user)
            if password:
                self.app.userdb.set_password(username, password, old_password=None)
            user.user_root = user_root
            user.is_admin = is_admin
            # Avoid updating the email fields is it didn'T changed. see pdsl/minarca#187
            if email != user.email:
                user.email = email
            success = _("User information modified successfully.")

            # Check and update user directory
            if user.user_root:
                self._check_user_root_dir(user.user_root)
                rdw_spider_repos.find_repos_for_user(user)

        elif action == "add":

            if username == "":
                raise RdiffWarning(_("The username is invalid."))
            logger.info("adding user [%s]", username)

            user = self.app.userdb.add_user(username, password)
            if user_root:
                user.user_root = user_root
            user.is_admin = is_admin
            user.email = email

            # Check and update user directory
            if user.user_root:
                self._check_user_root_dir(user.user_root)
                rdw_spider_repos.find_repos_for_user(user)
            success = _("User added successfully.")

        if action == "delete":
            user = self.app.userdb.get_user(username)
            if username == self.app.currentuser.username:
                raise RdiffWarning(_("You cannot remove your own account!."))
            logger.info("deleting user [%s]", username)
            self.app.userdb.delete_user(user)
            success = _("User account removed.")

        # Return messages
        return {'success': success}
