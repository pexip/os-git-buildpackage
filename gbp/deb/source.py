# vim: set fileencoding=utf-8 :
#
# (C) 2013 Guido Günther <agx@sigxcpu.org>
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
"""provides some debian source package related helpers"""

import os
from gbp.deb.format import DebianSourceFormat
from gbp.deb.changelog import ChangeLog

class FileVfs(object):
    def __init__(self, dir):
        """
        Access files in a unpaced Debian source package.

        @param dir: the toplevel of the source tree
        @type dir: C{str}
        """
        self._dir = dir

    def open(self, path, flags=None):
        flags = flags or 'r'
        return open(os.path.join(self._dir, path), flags)

class DebianSourceError(Exception):
    pass

class DebianSource(object):
    """
    A debianized source tree

    Querying/setting information in a debianized source tree
    involves several files. This class provides a common interface.
    """
    def __init__(self, vfs):
        """
        @param vfs: a class that implemented GbpVFS interfacce or
             a directory (which will used the DirGbpVFS class.
        """
        self._changelog = None

        if isinstance(vfs, basestring):
            self._vfs = FileVfs(vfs)
        else:
            self._vfs = vfs

    def is_native(self, has_upstream):
        """
        Whether this is a native debian package
        """
        try:
            ff = self._vfs.open('debian/source/format')
            f = DebianSourceFormat(ff.read())
            if f.type:
                return f.type == 'native'
        except IOError as e:
            pass # No format file; consider format 1.0

        # We actually have no way of knowing whether this is a native package 
        # or not at this point. Although policy indicates that native packages 
        # may not have a - in the version string, reality differs -- see 
        # lintian's native-package-with-dash-version tag. Thus, inspecting
        # the version is not robust. Instead, consider this a native package
        # if the caller has declared that there's no upstream available.
        return not has_upstream

    @property
    def changelog(self):
        """
        Return the L{gbp.deb.ChangeLog}
        """
        if not self._changelog:
            try:
                clf = self._vfs.open('debian/changelog')
                self._changelog = ChangeLog(clf.read())
            except IOError as err:
                raise DebianSourceError('Failed to read changelog: %s' % err)
        return self._changelog

    @property
    def sourcepkg(self):
        """
        The source package's name
        """
        return self.changelog['Source']
