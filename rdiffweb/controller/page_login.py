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
import logging

import cherrypy
from wtforms.fields import PasswordField, StringField
from wtforms.fields.simple import HiddenField
from wtforms.validators import InputRequired

from rdiffweb.controller import Controller, flash
from rdiffweb.controller.form import CherryForm
from rdiffweb.core.config import Option
from rdiffweb.tools.i18n import gettext_lazy as _

# Define the logger
logger = logging.getLogger(__name__)


class LoginForm(CherryForm):
    login = StringField(
        _('Username'),
        validators=[InputRequired()],
        render_kw={
            "placeholder": _('Username'),
            "autocorrect": "off",
            "autocapitalize": "none",
            "autocomplete": "off",
            "autofocus": "autofocus",
        },
    )
    password = PasswordField(_('Password'), validators=[InputRequired()], render_kw={"placeholder": _('Password')})
    # Sanitize the redirect URL to avoid Open Redirect
    redirect = HiddenField(default='/', filters=[lambda v: v if v.startswith('/') else '/'])


class LoginPage(Controller):
    """
    This page is used by the authentication to enter a user/pass.
    """

    _welcome_msg = Option("welcome_msg")

    @cherrypy.expose()
    @cherrypy.config(**{'tools.auth_form.on': False, 'tools.ratelimit.on': True})
    def index(self, **kwargs):
        form = LoginForm()

        # Redirect user to main page if already login.
        if self.app.currentuser is not None:
            raise cherrypy.HTTPRedirect(form.redirect.data or '/')

        # Validate user's credentials
        if form.validate_on_submit():
            try:
                login = any(cherrypy.engine.publish('login', form.login.data, form.password.data))
            except Exception:
                logger.exception('fail to validate credential')
                flash(_("Fail to validate user credential."))
            else:
                if login:
                    raise cherrypy.HTTPRedirect(form.redirect.data or '/')
                else:
                    flash(_("Invalid username or password."))

        params = {'form': form}

        # Add welcome message to params. Try to load translated message.
        if self._welcome_msg:
            params["welcome_msg"] = self._welcome_msg.get('')
            if hasattr(cherrypy.response, 'i18n'):
                locale = cherrypy.response.i18n.locale.language
                params["welcome_msg"] = self._welcome_msg.get(locale, params["welcome_msg"])

        return self._compile_template("login.html", **params).encode("utf-8")


class LogoutPage(Controller):
    @cherrypy.expose
    @cherrypy.config(**{'tools.auth_form.on': False})
    def default(self):
        cherrypy.session.clear()
        raise cherrypy.HTTPRedirect('/')
