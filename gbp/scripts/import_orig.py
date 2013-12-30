# vim: set fileencoding=utf-8 :
#
# (C) 2006, 2007, 2009, 2011 Guido Guenther <agx@sigxcpu.org>
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
#
"""Import a new upstream version into a git repository"""

import ConfigParser
import os
import sys
import tempfile
import gbp.command_wrappers as gbpc
from gbp.deb import (DebianPkgPolicy, parse_changelog_repo)
from gbp.deb.uscan import (Uscan, UscanError)
from gbp.deb.changelog import ChangeLog, NoChangeLogError
from gbp.deb.git import (GitRepositoryError, DebianGitRepository)
from gbp.config import GbpOptionParserDebian, GbpOptionGroup, no_upstream_branch_msg
from gbp.errors import GbpError
import gbp.log
from gbp.scripts.common.import_orig import (OrigUpstreamSource, cleanup_tmp_tree,
                                            ask_package_name, ask_package_version,
                                            repack_source, is_link_target)


class SourceImportManager(object):
    """Object to manage the import of an upstream source."""
    def __init__(self, path):
        self.name = None
        self.version = None
        self.component = None

        self._commit = None
        self._linked = False
        self._pristine_orig = None
        self._source = OrigUpstreamSource(path)
        self._tag = None
        self._tmpdir = None


    def detect_name_version_and_component(self, repo, options):
        # Guess defaults for the package name, version, and component from the
        # original tarball.
        (guessed_package, guessed_version, guessed_component) = self._source.guess_version() or ('', '', '')

        # Try to find the source package name
        try:
            cp = ChangeLog(filename='debian/changelog')
            sourcepackage = cp['Source']
        except NoChangeLogError:
            try:
                # Check the changelog file from the repository, in case
                # we're not on the debian-branch (but upstream, for
                # example).
                cp = parse_changelog_repo(repo, options.debian_branch, 'debian/changelog')
                sourcepackage = cp['Source']
            except NoChangeLogError:
                if options.interactive:
                    sourcepackage = ask_package_name(guessed_package,
                                                     DebianPkgPolicy.is_valid_packagename,
                                                     DebianPkgPolicy.packagename_msg)
                else:
                    if guessed_package:
                        sourcepackage = guessed_package
                    else:
                        raise GbpError("Couldn't determine upstream package name. Use --interactive.")

        # Try to find the version.
        if options.version:
            version = options.version
        else:
            if options.interactive:
                version = ask_package_version(guessed_version,
                                              DebianPkgPolicy.is_valid_upstreamversion,
                                              DebianPkgPolicy.upstreamversion_msg)
            else:
                if guessed_version:
                    version = guessed_version
                else:
                    raise GbpError("Couldn't determine upstream version. Use '-u<version>' or --interactive.")

        self.name = sourcepackage
        self.version = version
        self.component = guessed_component

    def unpack_or_repack_as_necessary(self, options):
        if not self._source.is_dir():
            self._tmpdir = tempfile.mkdtemp(dir='../')
            self._source.unpack(self._tmpdir, options.filters, self.component == '')
            gbp.log.debug("Unpacked '%s' to '%s'" % (self._source.path, self._source.unpacked))

        if self._source.needs_repack(options):
            gbp.log.debug("Filter pristine-tar: repacking '%s' from '%s'" % (self._source.path, self._source.unpacked))
            (self._source, self._tmpdir)  = repack_source(self._source, self.name, self.version, self.component, self._tmpdir, options.filters)

        # Don't mess up our repo with git metadata from an upstream tarball
        try:
            if os.path.isdir(os.path.join(self._source.unpacked, '.git/')):
                raise GbpError("The orig tarball contains .git metadata - giving up.")
        except OSError:
            pass

    def import_into_upstream_branch(self, repo, options):
        self._prepare_pristine_tar()

        try:
            upstream_branch = options.upstream_branch
            if self.component:
                upstream_branch += '-' + self.component

            filter_msg = ["", " (filtering out %s)"
                              % options.filters][len(options.filters) > 0]
            gbp.log.info("Importing '%s' to branch '%s'%s..." % (self._source.path,
                                                                 upstream_branch,
                                                                 filter_msg))
            gbp.log.info("Source package is %s" % self.name)
            gbp.log.info("Upstream version is %s" % self.version)

            msg = upstream_import_commit_msg(options, self.version)

            if options.vcs_tag:
                parents = [repo.rev_parse("%s^{}" % options.vcs_tag)]
            else:
                parents = None

            self._commit = repo.commit_dir(self._source.unpacked,
                                           msg=msg,
                                           branch=upstream_branch,
                                           other_parents=parents,
                                           create_missing_branch=True,
                                           )

            if options.pristine_tar:
                if self._pristine_orig:
                    repo.pristine_tar.commit(self._pristine_orig, upstream_branch)
                else:
                    gbp.log.warn("'%s' not an archive, skipping pristine-tar" % self._source.path)

            tagcomponent = ''
            if self.component:
                tagcomponent = '-' + self.component

            self._tag = repo.version_to_tag(options.upstream_tag, self.version, tagcomponent)
            repo.create_tag(name=self._tag,
                            msg="Upstream version %s" % self.version,
                            commit=self._commit,
                            sign=options.sign_tags,
                            keyid=options.keyid)
        except GitRepositoryError as err:
            msg = err.__str__() if len(err.__str__()) else ''
            raise GbpError("Import of %s failed: %s" % (source.path, msg))


    def merge_into_debian_branch(self, repo, options):
        try:
            if not repo.has_branch(options.debian_branch):
                repo.create_branch(options.debian_branch, rev=self._commit)
                repo.force_head(options.debian_branch, hard=True)
            else:
                gbp.log.info("Merging '%s' to '%s'" % ( self._tag, options.debian_branch ))
                repo.set_branch(options.debian_branch)
                try:
                    repo.merge(self._tag)
                except GitRepositoryError:
                    raise GbpError("Merge failed, please resolve.")
        except GitRepositoryError as err:
            msg = err.__str__() if len(err.__str__()) else ''
            raise GbpError("Import of %s failed: %s" % (source.path, msg))


    def cleanup(self, options):
        if self._pristine_orig and self._linked and not options.symlink_orig:
            os.unlink(self._pristine_orig)

        if self._tmpdir:
            cleanup_tmp_tree(self._tmpdir)


    def _prepare_pristine_tar(self):
        """
        Prepare the upstream source for pristine tar import.

        This checks if the upstream source is actually a tarball
        and creates a symlink from I{archive}
        to I{<pkg>_<version>.orig.tar.<ext>} so pristine-tar will
        see the correct basename.
        """
        if os.path.isdir(self._source.path):
            return

        ext = os.path.splitext(self._source.path)[1]
        if ext in ['.tgz', '.tbz2', '.tlz', '.txz' ]:
            ext = ".%s" % ext[2:]

        if not self.component:
            link = "../%s_%s.orig.tar%s" % (self.name, self.version, ext)
        else:
            link = "../%s_%s.orig-%s.tar%s" % ( self.name, self.version, self.component, ext)

        if os.path.basename(self._source.path) != os.path.basename(link):
            try:
                if not is_link_target(self._source.path, link):
                    os.symlink(os.path.abspath(self._source.path), link)
                    self._linked = True
            except OSError as err:
                    raise GbpError("Cannot symlink '%s' to '%s': %s" % (self._source.path, link, err[1]))
            self._pristine_orig = link
        else:
            self._pristine_orig = self._source.path


def upstream_import_commit_msg(options, version):
    return options.import_msg % dict(version=version)




def find_sources(options, args):
    """Find the tarball(s) to import - either via uscan or via command line argument
    @return: list of upstream source filenames or None if nothing to import
    @rtype: list of strings
    @raise GbpError: raised on all detected errors
    """
    if options.uscan: # uscan mode
        uscan = Uscan()

        if args:
            raise GbpError("you can't pass both --uscan and a filename.")

        gbp.log.info("Launching uscan...")
        try:
            uscan.scan()
        except UscanError as e:
            raise GbpError("%s" % e)

        if not uscan.uptodate:
            if uscan.tarball:
                gbp.log.info("using %s" % uscan.tarball)
                args.append(uscan.tarball)
            else:
                raise GbpError("uscan didn't download anything, and no source was found in ../")
        else:
            gbp.log.info("package is up to date, nothing to do.")
            return None
    if len(args) == 0:
        raise GbpError("No archive to import specified. Try --help.")

    return [ SourceImportManager(arg) for arg in args ]


def set_bare_repo_options(options):
    """Modify options for import into a bare repository"""
    if options.pristine_tar or options.merge:
        gbp.log.info("Bare repository: setting %s%s options"
                      % (["", " '--no-pristine-tar'"][options.pristine_tar],
                         ["", " '--no-merge'"][options.merge]))
        options.pristine_tar = False
        options.merge = False


def parse_args(argv):
    try:
        parser = GbpOptionParserDebian(command=os.path.basename(argv[0]), prefix='',
                                       usage='%prog [options] /path/to/upstream-version.tar.gz | --uscan')
    except ConfigParser.ParsingError as err:
        gbp.log.err(err)
        return None, None

    import_group = GbpOptionGroup(parser, "import options",
                      "pristine-tar and filtering")
    tag_group = GbpOptionGroup(parser, "tag options",
                      "options related to git tag creation")
    branch_group = GbpOptionGroup(parser, "version and branch naming options",
                      "version number and branch layout options")
    cmd_group = GbpOptionGroup(parser, "external command options", "how and when to invoke external commands and hooks")

    for group in [import_group, branch_group, tag_group, cmd_group ]:
        parser.add_option_group(group)

    branch_group.add_option("-u", "--upstream-version", dest="version",
                      help="Upstream Version")
    branch_group.add_config_file_option(option_name="debian-branch",
                      dest="debian_branch")
    branch_group.add_config_file_option(option_name="upstream-branch",
                      dest="upstream_branch")
    branch_group.add_option("--upstream-vcs-tag", dest="vcs_tag",
                            help="Upstream VCS tag add to the merge commit")
    branch_group.add_boolean_config_file_option(option_name="merge", dest="merge")

    tag_group.add_boolean_config_file_option(option_name="sign-tags",
                      dest="sign_tags")
    tag_group.add_config_file_option(option_name="keyid",
                      dest="keyid")
    tag_group.add_config_file_option(option_name="upstream-tag",
                      dest="upstream_tag")
    import_group.add_config_file_option(option_name="filter",
                      dest="filters", action="append")
    import_group.add_boolean_config_file_option(option_name="pristine-tar",
                      dest="pristine_tar")
    import_group.add_boolean_config_file_option(option_name="filter-pristine-tar",
                      dest="filter_pristine_tar")
    import_group.add_config_file_option(option_name="import-msg",
                      dest="import_msg")
    import_group.add_boolean_config_file_option(option_name="symlink-orig",
                                                dest="symlink_orig")
    cmd_group.add_config_file_option(option_name="postimport", dest="postimport")

    parser.add_boolean_config_file_option(option_name="interactive",
                                          dest='interactive')
    parser.add_option("-v", "--verbose", action="store_true", dest="verbose", default=False,
                      help="verbose command execution")
    parser.add_config_file_option(option_name="color", dest="color", type='tristate')
    parser.add_config_file_option(option_name="color-scheme",
                                  dest="color_scheme")

    # Accepted for compatibility
    parser.add_option("--no-dch", dest='no_dch', action="store_true",
                      default=False, help="deprecated - don't use.")
    parser.add_option("--uscan", dest='uscan', action="store_true",
                      default=False, help="use uscan(1) to download the new tarball.")

    (options, args) = parser.parse_args(argv[1:])
    gbp.log.setup(options.color, options.verbose, options.color_scheme)

    if options.no_dch:
        gbp.log.warn("'--no-dch' passed. This is now the default, please remove this option.")

    return options, args


def main(argv):
    ret = 0

    (options, args) = parse_args(argv)
    try:
        sources = find_sources(options, args)
        if not sources:
            return ret
    except GbpError as err:
        if len(err.__str__()):
            gbp.log.err(err)
        return 1 

    try:
        try:
            repo = DebianGitRepository('.')
        except GitRepositoryError:
            raise GbpError("%s is not a git repository" % (os.path.abspath('.')))

        # an empty repo has no branches:
        initial_branch = repo.get_branch()
        is_empty = False if initial_branch else True
        initial_head = None if is_empty else repo.rev_parse('HEAD', short=40)

        (clean, out) = repo.is_clean()
        if not clean and not is_empty:
            gbp.log.err("Repository has uncommitted changes, commit these first: ")
            raise GbpError(out)

        if repo.bare:
            set_bare_repo_options(options)

        # Collect upstream branches, ensuring they're unique and exist if appropriate
        upstream_branches = []
        for source in sources:
            source.detect_name_version_and_component(repo, options)
            upstream_branch = options.upstream_branch
            if source.component:
                upstream_branch += '-' + source.component
            if upstream_branch in upstream_branches:
                raise GbpError("Duplicate component '%s'" % ( component, ))
            if not repo.has_branch(upstream_branch) and not is_empty:
                raise GbpError(no_upstream_branch_msg % upstream_branch)
            upstream_branches.append(upstream_branch)

        # Unpack/repack each source, ensuring that there's no git metadata present
        for source in sources:
            source.unpack_or_repack_as_necessary(options)

        # Import each source into the relevant upstream branch, and create tag
        for source in sources:
            source.import_into_upstream_branch(repo, options)

        # If merge has been requested, merge each upstream branch onto the debian branch
        # TODO: what happens if a merge fails?
        if options.merge:
            for source in sources:
                source.merge_into_debian_branch(repo, options)

        # If the repository is empty and master isn't the selected debian branch, merge onto master, too
        # TODO: what happens if a merge fails?
        if is_empty and options.debian_branch != 'master':
            options.debian_branch = 'master'
            for source in sources:
                source.merge_into_debian_branch(repo, options)

        # TODO: why is this conditional on merge?
        if options.merge and options.postimport:
            epoch = ''
            repo.set_branch(options.debian_branch)
            if os.access('debian/changelog', os.R_OK):
                # No need to check the changelog file from the
                # repository, since we're certain that we're on
                # the debian-branch
                cp = ChangeLog(filename='debian/changelog')
                if cp.has_epoch():
                    epoch = '%s:' % cp.epoch
            info = { 'version': "%s%s-1" % (epoch, sources[0].version) }
            env = { 'GBP_BRANCH': options.debian_branch }
            gbpc.Command(options.postimport % info, extra_env=env, shell=True)()

        if not is_empty:
            # Restore the working copy to the pre-import state
            current_head = repo.rev_parse('HEAD', short=40)
            if current_head != initial_head:
                repo.force_head(initial_head, hard=True)
    except (gbpc.CommandExecFailed, GbpError) as err:
        if len(err.__str__()):
            gbp.log.err(err)
        ret = 1
    finally:
        for source in sources:
            source.cleanup(options)

    if not ret:
        gbp.log.info("Successfully imported version %s of %s" % (sources[0].version, sources[0].name))
    return ret

if __name__ == "__main__":
    sys.exit(main(sys.argv))

# vim:et:ts=4:sw=4:et:sts=4:ai:set list listchars=tab\:»·,trail\:·:
