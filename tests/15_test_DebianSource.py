# vim: set fileencoding=utf-8 :
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
"""Test L{gbp.pq}"""

from . import context

import os
import testutils
from gbp.deb.source import DebianSource, DebianSourceError
from gbp.deb.format import DebianSourceFormat
from gbp.git.vfs import GitVfs

class TestDebianSource(testutils.DebianGitTestRepo):
    """Test L{gbp.deb.source}'s """

    def setUp(self):
        testutils.DebianGitTestRepo.setUp(self)
        context.chdir(self.repo.path)

    def test_is_native_file_3_file(self):
        """Test native package of format 3"""
        source = DebianSource('.')
        os.makedirs('debian/source')

        dsf = DebianSourceFormat.from_content("3.0", "native")
        self.assertEqual(dsf.type, 'native')
        self.assertTrue(source.is_native(False))

        dsf = DebianSourceFormat.from_content("3.0", "quilt")
        self.assertEqual(dsf.type, 'quilt')
        self.assertFalse(source.is_native(True))

    def test_is_native_fallback_file(self):
        """Test native package without a debian/source/format file"""
        source = DebianSource('.')
        os.makedirs('debian/')
        self.assertFalse(source.is_native(True))
        self.assertTrue(source.is_native(False))

    def _commit_format(self, version, format):
        # Commit a format file to disk
        if not os.path.exists('debian/source'):
            os.makedirs('debian/source')
        dsf = DebianSourceFormat.from_content(version, format)
        self.assertEqual(dsf.type, format)
        self.repo.add_files('.')
        self.repo.commit_all('foo')
        os.unlink('debian/source/format')
        self.assertFalse(os.path.exists('debian/source/format'))

    def test_is_native_file_3_git(self):
        """Test native package of format 3 from git"""
        self._commit_format('3.0', 'native')
        source = DebianSource(GitVfs(self.repo))
        self.assertTrue(source.is_native(False))

        self._commit_format('3.0', 'quilt')
        source = DebianSource(GitVfs(self.repo))
        self.assertFalse(source.is_native(True))
