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
import os

import cherrypy
from sqlalchemy import Column, Integer, SmallInteger, String, and_, event, inspect, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

import rdiffweb.tools.db  # noqa
from rdiffweb.core import authorizedkeys
from rdiffweb.core.passwd import check_password, hash_password
from rdiffweb.tools.i18n import ugettext as _

from ._repo import RepoObject
from ._sshkeys import SshKey

logger = logging.getLogger(__name__)

Base = cherrypy.tools.db.get_base()

SEP = b'/'


class DuplicateSSHKeyError(Exception):
    """
    Raised by add_authorizedkey when trying to add the same SSH Key twice.
    """

    pass


class UserObject(Base):
    __tablename__ = 'users'
    __table_args__ = {'sqlite_autoincrement': True}

    ADMIN_ROLE = 0
    MAINTAINER_ROLE = 5
    USER_ROLE = 10
    ROLES = {
        'admin': ADMIN_ROLE,
        'maintainer': MAINTAINER_ROLE,
        'user': USER_ROLE,
    }

    userid = Column('UserID', Integer, primary_key=True)
    _username = Column('Username', String, nullable=False, unique=True)
    hash_password = Column('Password', String, nullable=False, default="")
    _user_root = Column('UserRoot', String, nullable=False, default="")
    _is_admin = Column(
        'IsAdmin',
        SmallInteger,
        nullable=False,
        server_default="0",
        doc="DEPRECATED This column is replaced by 'role'",
    )
    _email = Column('UserEmail', String, nullable=False, default="")
    restore_format = Column(
        'RestoreFormat',
        SmallInteger,
        nullable=False,
        server_default="1",
        doc="DEPRECATED This column is not used anymore",
    )
    _role = Column('role', SmallInteger, nullable=False, server_default=str(USER_ROLE))
    fullname = Column('fullname', String, nullable=False, default="")
    repo_objs = relationship(
        'RepoObject',
        foreign_keys='UserObject.userid',
        primaryjoin='UserObject.userid == RepoObject.userid',
        uselist=True,
        lazy=True,
        order_by=lambda: RepoObject.repopath,
    )

    @classmethod
    def get_user(cls, user):
        """Return a user object."""
        return UserObject.query.filter(UserObject.username == user).first()

    @classmethod
    def create_admin_user(cls, default_username, default_password):
        # Check if admin user exists. If not, created it.
        userobj = UserObject.get_user(default_username)
        if not userobj:
            userobj = cls.add_user(default_username, role=UserObject.ADMIN_ROLE, user_root='/backups')
        # Also make sure to update the password with latest value from config file.
        if default_password and default_password.startswith('{SSHA}'):
            userobj.hash_password = default_password
        elif default_password:
            userobj.hash_password = hash_password(default_password)
        else:
            userobj.hash_password = hash_password('admin123')
        userobj.add()

    @classmethod
    def add_user(cls, username, password=None, **attrs):
        """
        Used to add a new user with an optional password.
        """
        assert password is None or isinstance(password, str)
        # Check if user already exists.
        if UserObject.get_user(username):
            raise ValueError(_("User %s already exists." % (username,)))

        # Find a database where to add the user
        logger.info("adding new user [%s]", username)
        userobj = UserObject(
            username=username,
            hash_password=hash_password(password) if password else '',
            **attrs,
        ).add()
        # Raise event
        cherrypy.engine.publish('user_added', userobj)
        # Return user object
        return userobj

    def add_authorizedkey(self, key, comment=None):
        """
        Add the given key to the user. Adding the key to his `authorized_keys`
        file if it exists and adding it to database.
        """
        # Parse and validate ssh key
        assert key
        key = authorizedkeys.check_publickey(key)

        # Remove option, replace comments.
        key = authorizedkeys.AuthorizedKey(
            options=None, keytype=key.keytype, key=key.key, comment=comment or key.comment
        )

        # If a filename exists, use it by default.
        filename = os.path.join(self.user_root, '.ssh', 'authorized_keys')
        if os.path.isfile(filename):
            with open(filename, mode="r+", encoding='utf-8') as fh:
                if authorizedkeys.exists(fh, key):
                    raise DuplicateSSHKeyError(_("SSH key already exists"))
                logger.info("add key [%s] to [%s] authorized_keys", key, self.username)
                authorizedkeys.add(fh, key)
        else:
            # Also look in database.
            logger.info("add key [%s] to [%s] database", key, self.username)
            try:
                SshKey(userid=self.userid, fingerprint=key.fingerprint, key=key.getvalue()).add()
            except IntegrityError:
                SshKey.session.rollback()
                raise DuplicateSSHKeyError(
                    _("Duplicate key. This key already exists or is associated to another user.")
                )
        cherrypy.engine.publish('user_attr_changed', self, {'authorizedkeys': True})

    def valid_user_root(self):
        """
        Check if the current user_root is valid and readable
        """
        try:
            return os.access(self.user_root, os.F_OK) and os.path.isdir(self.user_root)
        except Exception:
            return False

    def delete(self, *args, **kwargs):
        cfg = cherrypy.tree.apps[''].cfg
        if self.username == cfg.admin_user:
            raise ValueError(_("can't delete admin user"))
        # FIXME This should be deleted by cascade
        SshKey.query.filter(SshKey.userid == self.userid).delete()
        RepoObject.query.filter(RepoObject.userid == self.userid).delete()
        # Delete ourself
        Base.delete(self)

    def delete_authorizedkey(self, fingerprint):
        """
        Remove the given key from the user. Remove the key from his
        `authorized_keys` file if it exists and from database database.
        """
        # If a filename exists, use it by default.
        filename = os.path.join(self.user_root, '.ssh', 'authorized_keys')
        if os.path.isfile(filename):
            with open(filename, mode='r+', encoding='utf-8') as fh:
                logger.info("removing key [%s] from [%s] authorized_keys", fingerprint, self.username)
                authorizedkeys.remove(fh, fingerprint)
        else:
            # Also look in database.
            logger.info("removing key [%s] from [%s] database", fingerprint, self.username)
            SshKey.query.filter(and_(SshKey.userid == self.userid, SshKey.fingerprint == fingerprint)).delete()
        cherrypy.engine.publish('user_attr_changed', self, {'authorizedkeys': True})

    @property
    def disk_usage(self):
        # Skip if user_root is invalid.
        if not self.user_root or not os.path.exists(self.user_root):
            return 0
        values = cherrypy.engine.publish('get_disk_usage', self)
        # Return the first not None value
        return next((v for v in values if v is not None), 0)

    @property
    def disk_quota(self):
        # Skip if user_root is invalid.
        if not self.user_root or not os.path.exists(self.user_root):
            return 0
        values = cherrypy.engine.publish('get_disk_quota', self)
        # Return the first not None value
        return next((v for v in values if v is not None), 0)

    @disk_quota.setter
    def disk_quota(self, value):
        # Skip if user_root is invalid.
        if not self.user_root or not os.path.exists(self.user_root):
            return
        cherrypy.engine.publish('set_disk_quota', self, value)

    @property
    def authorizedkeys(self):
        """
        Return an iterator on the authorized key. Either from his
        `authorized_keys` file if it exists or from database.
        """
        # If a filename exists, use it by default.
        filename = os.path.join(self.user_root, '.ssh', 'authorized_keys')
        if os.path.isfile(filename):
            for k in authorizedkeys.read(filename):
                yield k

        # Also look in database.
        for record in SshKey.query.filter(SshKey.userid == self.userid).all():
            yield authorizedkeys.check_publickey(record.key)

    def refresh_repos(self, delete=False):
        """
        Return list of repositories object to reflect the filesystem folders.

        Return a RepoObject for each sub directories under `user_root` with `rdiff-backup-data`.
        """
        # Update the repositories by walking in the directory tree.
        def _onerror(unused):
            logger.error('error updating user [%s] repos' % self.username, exc_info=1)

        # Get application config
        cfg = cherrypy.tree.apps[''].cfg

        dirty = False
        records = RepoObject.query.filter(RepoObject.userid == self.userid).order_by(RepoObject.repopath).all()
        user_root = os.fsencode(self.user_root)
        for root, dirs, unused_files in os.walk(user_root, _onerror):
            for name in dirs.copy():
                if name.startswith(b'.'):
                    dirs.remove(name)
            if b'rdiff-backup-data' in dirs:
                repopath = os.path.relpath(root, start=user_root)
                del dirs[:]
                # Handle special scenario when the repo is the
                # user_root
                repopath = b'' if repopath == b'.' else repopath

                # Check if repo path exists.
                record_match = next((record for record in records if record.repopath == os.fsdecode(repopath)), None)
                if not record_match:
                    # Add repository to database.
                    RepoObject(user=self, repopath=os.fsdecode(repopath)).add()
                    dirty = True
                else:
                    records.remove(record_match)
            if root.count(SEP) - user_root.count(SEP) >= cfg.max_depth:
                del dirs[:]
        # If enabled, remove entried from database
        if delete:
            for record in records:
                RepoObject.query.filter(RepoObject.repoid == record.repoid).delete()
        return dirty

    @hybrid_property
    def is_admin(self):
        return self.role <= self.ADMIN_ROLE

    @hybrid_property
    def is_ldap(self):
        return self.hash_password is None or self.hash_password == ''

    @is_ldap.expression
    def is_ldap(cls):
        return or_(cls.hash_password.is_(None), cls.hash_password == '')

    @hybrid_property
    def is_maintainer(self):
        return self.role <= self.MAINTAINER_ROLE

    def set_password(self, password, old_password=None):
        """
        Change the user's password. Raise a ValueError if the username or
        the password are invalid.
        """
        assert isinstance(password, str)
        assert old_password is None or isinstance(old_password, str)
        if not password:
            raise ValueError("password can't be empty")

        # Cannot update admin-password if defined
        cfg = cherrypy.tree.apps[''].cfg
        if self.username == cfg.admin_user and cfg.admin_password:
            raise ValueError(_("can't update admin-password defined in configuration file"))

        if old_password and not check_password(old_password, self.hash_password):
            raise ValueError(_("Wrong password"))

        logger.info("updating user password [%s]", self.username)
        self.hash_password = hash_password(password)

    def __eq__(self, other):
        return type(self) == type(other) and inspect(self).key == inspect(other).key

    @hybrid_property
    def username(self):
        return self._username

    @username.setter
    def username(self, value):
        oldvalue = self._username
        self._username = value
        if oldvalue != value:
            cherrypy.engine.publish('user_attr_changed', self, {'username': (oldvalue, value)})

    @hybrid_property
    def role(self):
        if self._role is None:
            return self.USER_ROLE
        return self._role

    @role.setter
    def role(self, value):
        oldvalue = self._role
        self._role = value
        if oldvalue != value:
            cherrypy.engine.publish('user_attr_changed', self, {'role': (oldvalue, value)})

    @hybrid_property
    def email(self):
        return self._email

    @email.setter
    def email(self, value):
        oldvalue = self._email
        self._email = value
        if oldvalue != value:
            cherrypy.engine.publish('user_attr_changed', self, {'email': (oldvalue, value)})

    @hybrid_property
    def user_root(self):
        return self._user_root

    @user_root.setter
    def user_root(self, value):
        oldvalue = self._user_root
        self._user_root = value
        if oldvalue != value:
            cherrypy.engine.publish('user_attr_changed', self, {'user_root': (oldvalue, value)})


@event.listens_for(UserObject.hash_password, "set")
def hash_password_set(target, value, oldvalue, initiator):
    if value and value != oldvalue:
        cherrypy.engine.publish('user_password_changed', target)


@event.listens_for(UserObject, 'after_delete')
def user_after_delete(mapper, connection, target):
    """
    Publish event when user is deleted.
    """
    cherrypy.engine.publish('user_deleted', target.username)
