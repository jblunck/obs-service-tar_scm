#!/usr/bin/env python

import argparse
import datetime
import os
import shutil
import re
import fnmatch
import sys
import tarfile
import subprocess
import atexit
import hashlib
import tempfile
import logging
import glob

def safe_run(cmd, cwd):

    logging.debug("COMMAND: %s" % cmd)
    proc = subprocess.Popen(cmd,
                            shell=False,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            cwd=cwd)
    proc.wait()
    output = proc.stdout.read().strip()
    if proc.returncode:
        logging.info("ERROR(%d): %s" % ( proc.returncode, output ))
        sys.exit("Command failed; aborting!")
    else:
        logging.debug("RESULT(%d): %s" % ( proc.returncode, output ))
    return (proc.returncode, output)

def fetch_upstream_git(url, clone_dir, revision, cwd):

    safe_run(['git', 'clone', url, clone_dir], cwd=cwd)
    safe_run(['git', 'submodule', 'update', '--init', '--recursive'], clone_dir)

def fetch_upstream_svn(url, clone_dir, revision, cwd):

    command = ['svn', 'checkout', '--non-interactive', url, clone_dir]
    if revision:
        command.insert(4, '-r%s' % revision)
    safe_run(command, cwd)

def fetch_upstream_hg(url, clone_dir, revision, cwd):

    safe_run(['hg', 'clone', url, clone_dir], cwd)

def fetch_upstream_bzr(url, clone_dir, revision, cwd):

    command = ['bzr', 'checkout', url, clone_dir]
    if revision:
        command.insert(3, '-r')
        command.insert(4, revision)
    safe_run(command, cwd)

fetch_upstream_commands = {
    'git': fetch_upstream_git,
    'svn': fetch_upstream_svn,
    'hg':  fetch_upstream_hg,
    'bzr': fetch_upstream_bzr,
}

def update_cache_git(url, clone_dir, revision):

    safe_run(['git', 'fetch'], cwd=clone_dir)

def update_cache_svn(url, clone_dir, revision):

    command = ['svn', 'update']
    if revision:
        command.insert(3, "-r%s" % revision)
    safe_run(command, cwd=clone_dir)

def update_cache_hg(url, clone_dir, revision):

    safe_run(['hg', 'pull'], cwd=clone_dir)

def update_cache_bzr(url, clone_dir, revision):

    command = ['bzr', 'update']
    if revision:
        command.insert(3, '-r')
        command.insert(4, revision)
    safe_run(command, cwd=clone_dir)

update_cache_commands = {
    'git': update_cache_git,
    'svn': update_cache_svn,
    'hg':  update_cache_hg,
    'bzr': update_cache_bzr,
}

def switch_revision_git(clone_dir, revision):

    if revision is None:
        revision = 'master'

    revs = [ x + revision for x in [ 'origin/', '' ]]
    for rev in revs:
        try:
            safe_run(['git', 'rev-parse', '--verify', '--quiet', rev],
                     cwd=clone_dir)
            p = safe_run(['git', 'reset', '--hard', rev], cwd=clone_dir)[1]
            logging.info(p)
            break
        except SystemExit:
            continue
    else:
        sys.exit('%s: No such revision' % revision)

    safe_run(['git', 'submodule', 'update', '--recursive'], cwd=clone_dir)


def switch_revision_hg(clone_dir, revision):

    if revision is None:
        revision = 'tip'

    try:
        safe_run(['hg', 'update', revision], cwd=clone_dir)
    except SystemExit:
        sys.exit('%s: No such revision' % revision)


def switch_revision_none(clone_dir, revision):

    return


switch_revision_commands = {
    'git': switch_revision_git,
    'svn': switch_revision_none,
    'hg':  switch_revision_hg,
    'bzr': switch_revision_none,
}

def fetch_upstream(scm, url, revision, out_dir):
    # calc_dir_to_clone_to
    basename = os.path.basename(re.sub(r'/.git$', '', url))
    clone_dir = os.path.abspath(os.path.join(out_dir, basename))

    if not os.path.isdir(clone_dir):
        # initial clone
        os.mkdir(clone_dir)
        fetch_upstream_commands[scm](url, clone_dir, revision, cwd=out_dir)
    else:
        logging.info("Detected cached repository...")
        update_cache_commands[scm](url, clone_dir, revision)

    # switch_to_revision
    switch_revision_commands[scm](clone_dir, revision)

    return clone_dir

def prep_tree_for_tar(repodir, subdir, outdir, dstname):

    src = os.path.join(repodir, subdir)
    if not os.path.exists(src):
        sys.exit("%s: No such file or directory" % src)

    dst = os.path.join(outdir, dstname)
    if os.path.exists(dst) and ( os.path.samefile(src, dst) or os.path.samefile(os.path.dirname(src), dst) ):
        sys.exit("%s: src and dst refer to same file" % src)

    shutil.copytree(src, dst)

    return dst


def create_tar(repodir, outdir, dstname, extension='tar',
               exclude=[], include=[]):

    ( workdir, topdir ) = os.path.split(repodir)


    incl_patterns = []
    excl_patterns = []

    for i in include:
        incl_patterns.append(re.compile(fnmatch.translate(i)))

    # skip vcs files base on this pattern
    excl_patterns.append(re.compile(r".*/\.bzr.*"))
    excl_patterns.append(re.compile(r".*/\.git.*"))
    excl_patterns.append(re.compile(r".*/\.hg.*"))
    excl_patterns.append(re.compile(r".*/\.svn.*"))

    for e in exclude:
        excl_patterns.append(re.compile(fnmatch.translate(e)))

    def tar_filter(tarinfo):
        tarinfo.uid = tarinfo.gid = 0
        tarinfo.uname = tarinfo.gname = "root"

        if incl_patterns:
            for p in incl_patterns:
                if p.match(tarinfo.name):
                    return tarinfo
            return None

        for p in excl_patterns:
            if p.match(tarinfo.name):
                return None
        return tarinfo

    os.chdir(workdir)

    tar = tarfile.open(os.path.join(outdir, dstname + '.' + extension), "w")
    tar.add(topdir, filter=tar_filter)
    tar.close()


cleanup_dirs = []

def cleanup(dirs):

    logging.info("Cleaning: %s" % ' '.join(dirs))
    for d in dirs:
        if not os.path.exists(d):
            continue
        for root, dirs, files in os.walk(d, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(d)

def version_iso_cleanup(version):

    version = re.sub(r'([0-9]{4})-([0-9]{2})-([0-9]{2}) +([0-9]{2})([:]([0-9]{2})([:]([0-9]{2}))?)?( +[-+][0-9]{3,4})', r'\1\2\3T\4\6\8', version)
    version = re.sub(r'[-:]', '', version)
    return version

def detect_version_git(repodir, versionformat):

    if versionformat is None:
        versionformat = '%ct'

    if re.match('.*@PARENT_TAG@.*', versionformat):
        try:
            p = safe_run(['git', 'describe', '--tags', '--abbrev=0'],
                         repodir)[1]
            versionformat = re.sub('@PARENT_TAG@', p, versionformat)
        except SystemExit:
            sys.exit('\e[0;31mThe git repository has no tags, thus @PARENT_TAG@ can not be expanded\e[0m')

    version = safe_run(['git', 'log', '-n1', '--date=short',
                        "--pretty=format:%s" % versionformat ], repodir)[1]
    return version_iso_cleanup(version)

def detect_version_svn(repodir, versionformat):

    if versionformat is None:
        versionformat = '%r'

    svn_info = safe_run([ 'svn', 'info' ], repodir)[1]

    version = ''
    m = re.search('Last Changed Rev: (.*)', svn_info, re.MULTILINE)
    if m:
        version = m.group(1).strip()
    return re.sub('%r', version, versionformat)

def detect_version_hg(repodir, versionformat):

    if versionformat is None:
        versionformat = '{rev}'

    version = safe_run([ 'hg', 'id', '-n',  ], repodir)[1]

    version = safe_run([ 'hg', 'log', '-l1', "-r%s" % version, '--template',
                         versionformat ], repodir)[1]
    return version_iso_cleanup(version)

def detect_version_bzr(repodir, versionformat):

    if versionformat is None:
        versionformat = '%r'

    version = safe_run([ 'bzr', 'revno' ], repodir)[1]
    return re.sub('%r', version, versionformat)

def detect_version(scm, repodir, versionformat=None):

    detect_version_commands = {
        'git': detect_version_git,
        'svn': detect_version_svn,
        'hg':  detect_version_hg,
        'bzr': detect_version_bzr,
    }

    version = detect_version_commands[scm](repodir, versionformat)
    logging.debug("VERSION(auto): %s" % version)
    return version

def get_repocache_hash(scm, url, subdir):

    m = hashlib.sha256()
    m.update(url)
    if scm == 'svn':
        m.update('/' + subdir)
    return m.hexdigest()

def detect_changes_revision(url, srcdir, outdir):

    change = {
        'revision': None,
        'url': url,
    }

    try:
        # If lxml is available, we can use a parser that doesnt destroy comments
        import lxml.etree as ET
        xml_parser = ET.XMLParser(remove_comments=False)
    except ImportError:
        import xml.etree.ElementTree as ET
        xml_parser = None

    create_servicedata, tar_scm_service = False, None
    tar_scm_xmlstring = "  <service name=\"tar_scm\">\n    <param name=\"url\">%s</param>\n  </service>\n" % url
    root=None
    try:
        tree = ET.parse(os.path.join(srcdir, "_servicedata"),
                        parser=xml_parser)
        root = tree.getroot()
        for service in root.findall("service[@name='tar_scm']"):
            for param in service.findall("param[@name='url']"):
                if param.text == url:
                    tar_scm_service = service
                    break
        if tar_scm_service is not None:
            params = tar_scm_service.findall("param[@name='changesrevision']")
            if len(params) == 1:
                # Found what we searched for!
                change['revision'] = params[0].text
        else:
            # File exists, is well-formed but does not contain the service we search
            root.append(ET.fromstring(tar_scm_xmlstring))
            create_servicedata = True
    except IOError as e:
        # File doesnt exist
        root = ET.fromstring("<servicedata>\n%s</servicedata>\n" % tar_scm_xmlstring)
        create_servicedata = True
    except ET.ParseError as e:
        if e.message.startswith("Document is empty"):
            # File is empty
            root = ET.fromstring("<servicedata>\n%s</servicedata>\n" % tar_scm_xmlstring)
            create_servicedata = True
        else:
            # File is corrupt
            raise

    if create_servicedata:
        ET.ElementTree(root).write(os.path.join(outdir, "_servicedata"))
    else:
        if not os.path.samefile(os.path.join(srcdir, "_servicedata"),
                                os.path.join(outdir, "_servicedata")):
            shutil.copy(os.path.join(srcdir, "_servicedata"),
                        os.path.join(outdir, "_servicedata"))

    return change

def write_changes_revision(url, outdir, revision):

    logging.debug("Updating %s" % os.path.join(outdir, '_servicedata'))

    try:
        # If lxml is available, we can use a parser that doesn't destroy comments
        import lxml.etree as ET
        xml_parser = ET.XMLParser(remove_comments=False)
    except ImportError:
        import xml.etree.ElementTree as ET
        xml_parser = None

    tree = ET.parse(os.path.join(outdir, "_servicedata"), parser=xml_parser)
    root = tree.getroot()
    changed, tar_scm_service = False, None
    for service in root.findall("service[@name='tar_scm']"):
        for param in service.findall("param[@name='url']"):
            if param.text == url:
                tar_scm_service = service
                break
    if tar_scm_service is not None:
        changerev_params = tar_scm_service.findall("param[@name='changesrevision']")
        if len(changerev_params) == 1:  # already present, just update
            if changerev_params[0].text != revision:
                changerev_params[0].text = revision
                changed = True
        else:  # not present, add changesrevision element
            tar_scm_service.append(ET.fromstring("    <param name=\"changesrevision\">%s</param>\n" % revision))
            changed = True
        if changed:
            tree.write(os.path.join(outdir, "_servicedata"))
    else:
        sys.exit("File _servicedata is missing tar_scm with URL '%s'" % url)

def write_changes(filename, changes, version, author):

    if changes is None:
        return

    if author is None:
        author = 'opensuse-packaging@opensuse.org'
    logging.debug("AUTHOR: %s" % author)

    logging.debug("Writing changes file " + filename)

    f = tempfile.NamedTemporaryFile(delete=False)
    f.write('-------------------------------------------------------------------\n')
    f.write("%s - %s\n" % (
        datetime.datetime.utcnow().strftime('%a %b %d %H:%M:%S UTC %Y'),
        author ))
    f.write('\n')
    f.write("- Update to version %s:\n" % version)
    for line in changes.split(os.linesep):
        f.write(" + %s\n" % line)
    f.write('\n')

    old_f = open(filename, 'r')
    f.write(old_f.read())
    old_f.close()

    f.close()

    os.rename(f.name, filename)

def detect_changes_commands_git(repodir, changes):

    last_rev = changes['revision']

    if last_rev is None:
        last_rev = safe_run(['git', 'log', '-n1', '--pretty=format:%H',
                               '--skip=10'], cwd=repodir)[1]
    current_rev = safe_run(['git', 'log', '-n1', '--pretty=format:%H'],
                          cwd=repodir)[1]

    if last_rev == current_rev:
        logging.debug("No new commits, skipping changes file generation")
        return

    logging.debug("Generating changes between %s and %s" %
                  ( last_rev , current_rev ))

    lines=safe_run(['git', 'log', '--no-merges', '--pretty=tformat:%s',
                    "%s..%s" % ( last_rev , current_rev )], repodir)[1]

    changes['revision'] = current_rev
    changes['lines'] = '\n'.join(reversed(lines.split('\n')))
    return changes

def detect_changes(scm, url, repodir, outdir):

    try:
        changes = detect_changes_revision(url, outdir, outdir)
    except Exception, e:
        sys.exit("_servicedata: Failed to parse (%s)" % e)

    detect_changes_commands = {
        'git': detect_changes_commands_git,
    }

    return detect_changes_commands[scm](repodir, changes)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Git Tarballs')
    parser.add_argument('--scm', required=True,
                        help='Used SCM')
    parser.add_argument('--url', required=True,
                        help='upstream tarball URL to download')
    parser.add_argument('--outdir', required=True,
                        help='osc service parameter that does nothing')
    parser.add_argument('--verbose', '-v', action='store_true', default=False,
                        help='enable verbose output')
    parser.add_argument('--version', default='_auto_',
                        help='Specify version to be used in tarball. Defaults to automatically detected value formatted by versionformat parameter.')
    parser.add_argument('--versionformat',
                        help='Auto-generate version from checked out source using this format string.  This parameter is used if the \'version\' parameter is not specified.')
    parser.add_argument('--changesgenerate', action='store_true', default=False,
                        help='osc service parameter that does nothing')
    parser.add_argument('--changesauthor',
                        help='osc service parameter that does nothing')
    parser.add_argument('--filename',
                        help='name of package - used together with version to determine tarball name')
    parser.add_argument('--extension', default='tar',
                        help='suffix name of package - used together with filename to determine tarball name')
    parser.add_argument('--revision',
                        help='revision to package')
    parser.add_argument('--subdir', default='',
                        help='package just a sub directory')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--include', action='append', default=[],
                       help='for specifying subset of files/subdirectories to pack in the tar ball')
    group.add_argument('--exclude', action='append', default=[],
                       help='for specifying excludes when creating the tar ball')
    parser.add_argument('--history-depth',
                        help='osc service parameter that does nothing')
    parser.add_argument('--submodules',
                        help='osc service parameter that does nothing')
    args = parser.parse_args()

    # basic argument validation
    if not os.path.isdir(args.outdir):
        sys.exit("%s: No such directory" % args.outdir);

    if args.history_depth:
        print "history-depth parameter is obsolete and will be ignored"

    FORMAT = "%(message)s"
    logging.basicConfig(format=FORMAT, stream=sys.stderr, level=logging.INFO)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # force cleaning of our workspace on exit
    atexit.register(cleanup, cleanup_dirs)

    # check for enabled caches
    repocachedir = os.getenv('CACHEDIRECTORY')

    # construct repodir (the parent directory of the checkout)
    repodir = None
    if repocachedir and os.path.isdir(os.path.join(repocachedir, 'repo')):
        repohash = get_repocache_hash(args.scm, args.url, args.subdir)
        logging.debug("HASH: %s" % repohash)
        repodir = os.path.join(repocachedir, 'repo')
        repodir = os.path.join(repodir, repohash)

    # if caching is enabled but we haven't cached something yet
    if repodir and not os.path.isdir(repodir):
        repodir = tempfile.mkdtemp(dir=os.path.join(repocachedir, 'incoming'))

    if repodir is None:
        repodir = tempfile.mkdtemp(dir=args.outdir)
        cleanup_dirs.append(repodir)


    clone_dir = fetch_upstream(args.scm, args.url, args.revision, repodir)

    if args.filename:
        dstname=args.filename
    else:
        dstname=os.path.basename(clone_dir)

    version = args.version
    if version == '_auto_' or args.versionformat:
        version = detect_version(args.scm, clone_dir, args.versionformat)
    if version:
        dstname=dstname + '-' + version

    logging.debug("DST: %s" % dstname)

    changes = None
    if args.changesgenerate:
        changes = detect_changes(args.scm, args.url, clone_dir, args.outdir)

    tar_dir = prep_tree_for_tar(clone_dir, args.subdir, args.outdir,
                                dstname=dstname)
    cleanup_dirs.append(tar_dir)

    create_tar(tar_dir, args.outdir,
               dstname=dstname, extension=args.extension,
               exclude=args.exclude, include=args.include)

    if changes:
        for filename in glob.glob(os.path.join(args.outdir, '*.changes')):
            write_changes(filename, changes['lines'], version,
                          args.changesauthor)
        write_changes_revision(changes['url'], args.outdir, changes['revision'])

    # Populate cache
    if repocachedir and os.path.isdir(os.path.join(repocachedir, 'repo')):
        repodir2 = os.path.join(repocachedir, 'repo')
        repodir2 = os.path.join(repodir2, repohash)
        if repodir2 and not os.path.isdir(repodir2):
            os.rename(repodir, repodir2)
        elif not os.path.samefile(repodir, repodir2):
            cleanup_dirs.append(repodir)
