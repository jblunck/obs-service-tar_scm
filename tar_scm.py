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

def fetch_upstream_git(url, clone_dir, revision):
    commands = [
        ['git', 'clone', url, clone_dir],
        ['git', 'submodule', 'update', '--init', '--recursive'],
    ]
    return commands

def fetch_upstream_svn(url, clone_dir, revision):
    command = [ ['svn', 'checkout', '--non-interactive', url, clone_dir] ]
    if revision:
        command[0].insert(4, '-r%s' % revision)
    return command

def fetch_upstream_hg(url, clone_dir, revision):
    command = [ ['hg', 'clone', url, clone_dir] ]
    return command

def fetch_upstream_bzr(url, clone_dir, revision):
    command = [ ['bzr', 'checkout', url, clone_dir] ]
    if revision:
        command[0].insert(3, '-r')
        command[0].insert(4, revision)
    return command

fetch_upstream_commands = {
    'git': fetch_upstream_git,
    'svn': fetch_upstream_svn,
    'hg': fetch_upstream_hg,
    'bzr': fetch_upstream_bzr,
}

def update_cache_bzr(url, clone_dir, revision):
    command = ['bzr', 'update']
    if revision:
        command.insert(3, '-r')
        command.insert(4, revision)
    return command

def update_cache_git(url, clone_dir, revision):
    command = ['git', 'fetch']
    return command

def update_cache_hg(url, clone_dir, revision):
    command = ['hg', 'pull']
    return command

def update_cache_svn(url, clone_dir, revision):
    command = ['svn', 'update']
    if revision:
        command.insert(3, "-r%s" % revision)
    return command


def switch_revision_git(clone_dir, revision):

    if revision is None:
        revision = 'master'

    # switch_to_revision
    revs = [ x + revision for x in [ 'origin/', '' ]]
    for rev in revs:
        if not subprocess.call(['git', 'rev-parse', '--verify', '--quiet', rev],
                               shell=False,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               cwd=clone_dir):
            # we want to see the output so do not pass stdout/stderr
            subprocess.call(['git', 'reset', '--hard', rev],
                            shell=False, cwd=clone_dir)
            break
    else:
        sys.exit('%s: No such revision' % revision)

    subprocess.call(['git', 'submodule', 'update', '--recursive'],
                    shell=False, cwd=clone_dir)


def switch_revision_hg(clone_dir, revision):

    if revision is None:
        revision = 'tip'

    if subprocess.call(['hg', 'update', revision],
                           shell=False, cwd=clone_dir):
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

        cmds = fetch_upstream_commands[scm](url, clone_dir, revision)
        print "CMD: %s" % cmds[0]
        proc = subprocess.Popen(cmds[0],
                                shell=False,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                cwd=out_dir)
        proc.wait()
        cmds.pop(0)
        for cmd in cmds:
            print "CMD: %s" % cmd
            proc = subprocess.Popen(cmd,
                                    shell=False,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    cwd=clone_dir)
            proc.wait()

    else:
        print "Detected cached repository..."

        update_cache_commands = {
            'git': update_cache_git,
            'svn': update_cache_svn,
            'hg':  update_cache_hg,
            'bzr': update_cache_bzr,
        }

        cmd = update_cache_commands[scm](url, clone_dir, revision)
        print 'COMMAND: %s' % cmd
        proc = subprocess.Popen(cmd,
                                shell=False,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                cwd=clone_dir)
        proc.wait()
        print 'STDOUT: %s' % proc.stdout.read()


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

    print "Cleaning: %s" % ' '.join(dirs)
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
        cmd = [ 'git', 'describe', '--tags', '--abbrev=0' ]
        proc = subprocess.Popen(cmd,
                                shell=False,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                cwd=repodir)
        proc.wait()
        if proc.returncode != 0:
            sys.exit('\e[0;31mThe git repository has no tags, thus @PARENT_TAG@ can not be expanded\e[0m')

        versionformat = re.sub('@PARENT_TAG@', proc.stdout.read().strip(), versionformat)

    cmd = [ 'git', 'log', '-n1', '--date=short',
            "--pretty=format:%s" % versionformat ]
    proc = subprocess.Popen(cmd,
                            shell=False,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            cwd=repodir)
    proc.wait()
    return version_iso_cleanup(proc.stdout.read().strip())

def detect_version_svn(repodir, versionformat):

    if versionformat is None:
        versionformat = '%r'

    cmd = [ 'svn', 'info' ]
    proc = subprocess.Popen(cmd,
                            shell=False,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            cwd=repodir)
    proc.wait()
    version = ''
    m = re.search('Last Changed Rev: (.*)', proc.stdout.read(), re.MULTILINE)
    if m:
        version = m.group(1).strip()
    return re.sub('%r', version, versionformat)

def detect_version_hg(repodir, versionformat):

    if versionformat is None:
        versionformat = '{rev}'

    cmd = [ 'hg', 'id', '-n',  ]
    proc = subprocess.Popen(cmd,
                            shell=False,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            cwd=repodir)
    proc.wait()
    version = proc.stdout.read().strip()

    cmd = [ 'hg', 'log', '-l1', "-r%s" % version, '--template', versionformat ]
    proc = subprocess.Popen(cmd,
                            shell=False,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            cwd=repodir)
    proc.wait()
    return version_iso_cleanup(proc.stdout.read().strip())

def detect_version_bzr(repodir, versionformat):

    if versionformat is None:
        versionformat = '%r'

    cmd = [ 'bzr', 'revno' ]
    proc = subprocess.Popen(cmd,
                            shell=False,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            cwd=repodir)
    proc.wait()
    version = proc.stdout.read().strip()
    return re.sub('%r', version, versionformat)

def detect_version(scm, repodir, versionformat=None):

    detect_version_commands = {
        'git': detect_version_git,
        'svn': detect_version_svn,
        'hg':  detect_version_hg,
        'bzr': detect_version_bzr,
    }

    version = detect_version_commands[scm](repodir, versionformat)
    print "VERSION(auto): %s" % version
    return version

def get_repocache_hash(scm, url, subdir):

    m = hashlib.sha256()
    m.update(url)
    if scm == 'svn':
        m.update('/' + subdir)
    return m.hexdigest()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Git Tarballs')
    parser.add_argument('--scm', required=True,
                        help='Used SCM')
    parser.add_argument('--url', required=True,
                        help='upstream tarball URL to download')
    parser.add_argument('--outdir', required=True,
                        help='osc service parameter that does nothing')
    parser.add_argument('--version', default='_auto_',
                        help='Specify version to be used in tarball. Defaults to automatically detected value formatted by versionformat parameter.')
    parser.add_argument('--versionformat',
                        help='Auto-generate version from checked out source using this format string.  This parameter is used if the \'version\' parameter is not specified.')
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

    # force cleaning of our workspace on exit
    atexit.register(cleanup, cleanup_dirs)

    # check for enabled caches
    repocachedir = os.getenv('CACHEDIRECTORY')

    # construct repodir (the parent directory of the checkout)
    repodir = None
    if repocachedir and os.path.isdir(os.path.join(repocachedir, 'repo')):
        repohash = get_repocache_hash(args.scm, args.url, args.subdir)
        print "HASH: %s" % repohash
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

    print "DST: %s" % dstname

    # detect_changes

    tar_dir = prep_tree_for_tar(clone_dir, args.subdir, args.outdir,
                                dstname=dstname)
    cleanup_dirs.append(tar_dir)

    create_tar(tar_dir, args.outdir,
               dstname=dstname, extension=args.extension,
               exclude=args.exclude, include=args.include)

    # Populate cache
    if repocachedir and os.path.isdir(os.path.join(repocachedir, 'repo')):
        repodir2 = os.path.join(repocachedir, 'repo')
        repodir2 = os.path.join(repodir2, repohash)
        if repodir2 and not os.path.isdir(repodir2):
            os.rename(repodir, repodir2)
        elif not os.path.samefile(repodir, repodir2):
            cleanup_dirs.append(repodir)
