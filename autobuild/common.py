#!/usr/bin/python
# $LicenseInfo:firstyear=2010&license=mit$
# Copyright (c) 2010, Linden Research, Inc.
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
# $/LicenseInfo$

"""
Low-level autobuild functionality common to all modules.

Any code that is potentially common to all autobuild sub-commands
should live in this module. This module should never depend on any
other autobuild module.

Importing this module will also guarantee that certain dependencies
are available, such as llbase

Author : Martin Reddy
Date   : 2010-04-13
"""

import os
import sys
import time
import glob
import itertools
import logging
import pprint
import socket
import shutil
import subprocess
import tarfile
import zipfile
import tempfile
import urllib2

from version import AUTOBUILD_VERSION_STRING

logger = logging.getLogger('autobuild.common')


class AutobuildError(RuntimeError):
    pass

# define the supported platforms
PLATFORM_DARWIN  = 'darwin'
PLATFORM_WINDOWS = 'windows'
PLATFORM_LINUX   = 'linux'
PLATFORM_SOLARIS = 'solaris'
PLATFORM_UNKNOWN = 'unknown'

PLATFORMS = [PLATFORM_DARWIN,
             PLATFORM_WINDOWS,
             PLATFORM_LINUX,
             PLATFORM_SOLARIS,
             ]


def get_current_platform():
    """
    Return appropriate the autobuild name for the current platform.
    """
    platform_map = {
        'darwin':  PLATFORM_DARWIN,
        'linux2':  PLATFORM_LINUX,
        'win32':   PLATFORM_WINDOWS,
        'cygwin':  PLATFORM_WINDOWS,
        'solaris': PLATFORM_SOLARIS
        }
    return os.environ.get('AUTOBUILD_PLATFORM_OVERRIDE', platform_map.get(sys.platform, PLATFORM_UNKNOWN))


def get_current_user():
    """
    Get the login name for the current user.
    """
    try:
        # Unix-only.
        import getpass
        return getpass.getuser()
    except ImportError:
        import getpass
        import ctypes
        MAX_PATH = 260                  # according to a recent WinDef.h
        name = ctypes.create_unicode_buffer(MAX_PATH)
        namelen = ctypes.c_int(len(name))  # len in chars, NOT bytes
        if not ctypes.windll.advapi32.GetUserNameW(name, ctypes.byref(namelen)):
            raise ctypes.WinError()
        return name.value


def get_default_scp_command():
    """
    Return the full path to the scp command
    """
    scp = find_executable(['pscp', 'scp'], ['.exe'])
    return scp


def get_autobuild_environment():
    """
    Return an environment under which to execute autobuild subprocesses.
    """
    return dict(os.environ, AUTOBUILD=os.environ.get(
        'AUTOBUILD', get_autobuild_executable_path()))


def get_install_cache_dir():
    """
    In general, the package archives do not change much, so find a 
    host/user specific location to cache files.
    """
    cache = os.getenv('AUTOBUILD_INSTALLABLE_CACHE')
    if cache is None:
        cache = get_temp_dir("install.cache")
    else:
        if not os.path.exists(cache):
            os.makedirs(cache, mode=0755)
    return cache


def get_s3_url():
    """
    Return the base URL for Amazon S3 package locations.
    """
    return "http://s3.amazonaws.com/viewer-source-downloads/install_pkgs"


def get_temp_dir(basename):
    """
    Return a temporary directory on the user's machine, uniquified
    with the specified basename string. You may assume that the
    directory exists.
    """
    user = get_current_user()
    if get_current_platform() == PLATFORM_WINDOWS:
        installdir = '%s.%s' % (basename, user)
        tmpdir = os.path.join(tempfile.gettempdir(), installdir)
    else:
        tmpdir = "/var/tmp/%s/%s" % (user, basename)
    if not os.path.exists(tmpdir):
        os.makedirs(tmpdir, mode=0755)
    return tmpdir


def get_autobuild_executable_path():
    if get_current_platform() == PLATFORM_WINDOWS:
        path = "%s.cmd" % sys.argv[0]
    else:
        path = sys.argv[0]
    return os.path.realpath(os.path.abspath(path))


def find_executable(executables, exts=None):
    """
    Given an executable name, or a list of executable names, return the
    name of the executable that can be found in the path. The names can
    have wildcards in them.

    exts can accept a list of extensions to search (e.g. [".exe", ".com"]).
    The empty extension (exact match for one of the names in executables) is
    always implied, but it's checked last.

    You can force find_executable() to consider only an exact match for one of
    the specified executables by passing exts=[].

    However, if exts is omitted (or, equivalently, None), the default is
    platform-sensitive. On Windows, find_executable() will look for some of
    the usual suspects (a subset of a typical PATHEXT value). On non-Windows
    platforms, the default is []. This allows us to place an extensionless
    script file 'foo' for most platforms, plus a 'foo.cmd' beside it for use
    on Windows.
    """
    if isinstance(executables, basestring):
        executables = [executables]
    if exts is None:
        exts = sys.platform.startswith("win") and [".com", ".exe", ".bat", ".cmd"] or []
    # The original implementation iterated over directories in PATH, checking
    # for each name in 'executables' in a given directory. This makes
    # intuitive sense -- but it's wrong. When 'executables' is (e.g.) ['pscp',
    # 'scp'] it means that if pscp exists on this platform, we need to
    # prioritize that over plain 'scp' -- even if the directory containing
    # plain 'scp' comes first. So the outer loop should be over 'executables'.
    for e in executables:
        for p in os.environ.get('PATH', "").split(os.pathsep):
            for ext in itertools.chain(exts, [""]):
                path = glob.glob(os.path.join(p, e + ext))
                if path:
                    return path[0]
    return None


def get_package_in_cache(package):
    """
    Return the filename of the package in the local cache.
    The file may not actually exist.
    """
    filename = os.path.basename(package)
    return os.path.join(get_install_cache_dir(), filename)


def is_package_in_cache(package):
    """
    Return True if the specified package has already been downloaded
    to the local package cache.
    """
    return os.path.exists(get_package_in_cache(package))


def compute_md5(path):
    """
    Returns the MD5 sum for the given file.
    """
    try:
        from hashlib import md5      # Python 2.6
    except ImportError:
        from md5 import new as md5   # Python 2.5 and earlier

    try:
        stream = open(path, 'rb')
    except IOError, err:
        raise AutobuildError("Can't compute MD5 for %s: %s" % (path, err))

    try:
        hasher = md5(stream.read())
    finally:
        stream.close()

    return hasher.hexdigest()


def does_package_match_md5(package, md5sum):
    """
    Returns True if the MD5 sum of the downloaded package archive
    matches the specified MD5 string.
    """
    return compute_md5(get_package_in_cache(package)) == md5sum


def download_package(package):
    """
    Download a package, specified as a URL, to the install cache.
    If the package already exists in the cache then this is a no-op.
    Returns False if there was a problem downloading the file.
    """

    # download timeout so a download doesn't hang
    download_timeout_seconds = 120
    download_timeout_retries = 5

    # save the old timeout
    old_download_timeout = socket.getdefaulttimeout()

    # have we already downloaded this file to the cache?
    cachename = get_package_in_cache(package)
    if os.path.exists(cachename):
        logger.info("package already in cache: %s" % cachename)
        return True

    # Set up the 'scp' handler
    opener = urllib2.build_opener()
    scp_or_http = __SCPOrHTTPHandler(get_default_scp_command())
    opener.add_handler(scp_or_http)
    urllib2.install_opener(opener)

    # Attempt to download the remote file
    logger.info("downloading %s to %s" % (package, cachename))
    result = True

    #
    # Exception handling:
    # 1. Isolate any exception from the setdefaulttimeout call.
    # 2. urllib2.urlopen supposedly wraps all errors in URLErrror. Include socket.timeout just in case.
    # 3. This code is here just to implement socket timeouts. The last general exception was already here so just leave it.
    #

    socket.setdefaulttimeout(download_timeout_seconds)

    for tries in itertools.count(1):
        try:
            file(cachename, 'wb').write(urllib2.urlopen(package).read())
            break
        except (socket.timeout, urllib2.URLError), e:
            if tries >= download_timeout_retries:
                result = False
                logger.exception("  error %s from class %s downloading package: %s"
                                 % (e, e.__class__.__name__, package))
                break
            logger.info("  error %s from class %s downloading package: %s. Retrying."
                        % (e, e.__class__.__name__, package))
            continue
        except Exception, e:
            logger.exception("error %s from class %s downloading package: %s. "
                             % (e, e.__class__.__name__, package))
            result = False
            break

    socket.setdefaulttimeout(old_download_timeout)

    # Clean up and return True if the download succeeded
    scp_or_http.cleanup()
    return result


def extract_package(package, install_dir, exclude=[]):
    """
    Extract the contents of a downloaded package to the specified
    directory.  Returns the list of files that were successfully
    extracted.
    """
    # Find the name of the package in the install cache
    return install_package(get_package_in_cache(package), install_dir, exclude=exclude)


def install_package(archive_path, install_dir, exclude=[]):
    """
    Install the archive at the provided path into the given installation directory.  Returns the
    list of files that were installed.
    """
    if not os.path.exists(archive_path):
        logger.error("cannot extract non-existing package: %s" % archive_path)
        return False
    logger.warn("extracting from %s" % os.path.basename(archive_path))
    if tarfile.is_tarfile(archive_path):
        return __extract_tar_file(archive_path, install_dir, exclude=exclude)
    elif zipfile.is_zipfile(archive_path):
        return __extract_zip_archive(archive_path, install_dir, exclude=exclude)
    else:
        logger.error("package %s is not archived in a supported format" % archive_path)
        return False


def extract_metadata_from_package(archive_path, metadata_file_name):
    """
    Get the package metadata from the archive
    """
    metadata_file = None
    if not os.path.exists(archive_path):
        logger.error("cannot extract metadata from non-existing package: %s" % archive_path)
        return False
    logger.debug("extracting metadata from %s" % os.path.basename(archive_path))
    if tarfile.is_tarfile(archive_path):
        tar = tarfile.open(archive_path, 'r')
        try:
            metadata_file = tar.extractfile(metadata_file_name)
        except KeyError, err:
            pass  # returning None will indicate that it was not there
    elif zipfile.is_zipfile(archive_path):
        try:
            zip = zipfile.ZipFile(archive_path, 'r')
            metadata_file = zip.open(metadata_file_name, 'r')
        except KeyError, err:
            pass  # returning None will indicate that it was not there
    else:
        logger.error("package %s is not archived in a supported format" % archive_path)
    return metadata_file


def remove_package(package):
    """
    Delete the downloaded package from the cache, if it exists there.
    """
    cachename = get_package_in_cache(package)
    if os.path.exists(cachename):
        os.remove(cachename)


def split_tarname(pathname):
    """
    Given a tarfile pathname of the form:
    "/some/path/boost-1.39.0-darwin-20100222a.tar.bz2"
    return the following:
    ("/some/path", ["boost", "1.39.0", "darwin", "20100222a"], ".tar.bz2")
    """
    # Split off the directory name from the unqualified filename.
    dir, filename = os.path.split(pathname)
    # dir = "/some/path"
    # filename = "boost-1.39.0-darwin-20100222a.tar.bz2"
    # Conceptually we want to split off the extension at this point. It would
    # be great to use os.path.splitext(). Unfortunately, at least as of Python
    # 2.5, os.path.splitext("woof.tar.bz2") returns ('woof.tar', '.bz2') --
    # not what we want. Instead, we'll have to split on '.'. But as the
    # docstring example points out, doing that too early would confuse things,
    # as there are dot characters in the embedded version number. So we have
    # to split on '-' FIRST.
    fileparts = filename.split('-')
    # fileparts = ["boost", "1.39.0", "darwin", "20100222a.tar.bz2"]
    # Almost there -- we just have to lop off the extension. NOW split on '.'.
    # We know there's at least fileparts[-1] because splitting a string with
    # no '-' -- even the empty string -- produces a list containing the
    # original string.
    extparts = fileparts[-1].split('.')
    # extparts = ["20100222a", "tar", "bz2"]
    # Replace the last entry in fileparts with the first part of extparts.
    fileparts[-1] = extparts[0]
    # Now fileparts = ["boost", "1.39.0", "darwin", "20100222a"] as desired.
    # Reconstruct the extension. To preserve the leading '.', don't just
    # delete extparts[0], replace it with the empty string. Yes, this does
    # assume that split() returns a list.
    extparts[0] = ""
    ext = '.'.join(extparts)
    # One more funky case. We've encountered "version numbers" like
    # "2009-08-30", "1-0" or "1.2-alpha". This would produce too many
    # fileparts, e.g. ["boost", "2009", "08", "30", "darwin", "20100222a"].
    # Detect that and recombine.
    if len(fileparts) > 4:
        fileparts[1:-2] = ['-'.join(fileparts[1:-2])]
    return dir, fileparts, ext


def search_up_for_file(path):
    """
    Search up the file tree for a file matching the base name of the path provided.

    Returns either the path to the file found or None if search fails.
    """
    path = os.path.abspath(path)
    filename = os.path.basename(path)
    dir = os.path.dirname(path)
    while not os.path.exists(os.path.join(dir, filename)):
        newdir = os.path.dirname(dir)
        if newdir == dir:
            return None
        dir = newdir
    return os.path.abspath(os.path.join(dir, filename))


class Serialized(dict, object):
    """
    A base class for serialized objects.  Regular attributes are stored in the inherited dictionary
    and will be serialized. Class variables will be handled normally and are not serialized.
    """

    def __getattr__(self, name):
        if name in self:
            return self[name]
        else:
            raise AttributeError("object has no attribute '%s'" % name)

    def __setattr__(self, name, value):
        if name in self.__class__.__dict__:
            self.__dict__[name] = value
        else:
            self[name] = value

    def copy(self):
        """
        Intercept attempts to copy like a dict, need to preserve leaf class
        instead of letting dict.copy() return a simple dict.
        """
        return self.__class__(self)


def select_directories(args, config, desc, verb, dir_from_config, platform=None):
    """
    Several of our subcommands provide the ability to specify an individual
    build tree on which to operate, or the build tree for each specified
    configuration, or --all build trees. Factor out the common selection logic.

    Returns: possibly-empty list of directories on which to operate.

    Pass:

    args: from argparse. Build your argparse subcommand arguments to set at least:
        select_dir: a specific individual directory (e.g. from "--install-dir")
        all: True when --all is specified
        configurations: list of configuration names, e.g. "Debug"

    config: loaded configuration file (from "autobuild.xml")

    desc: debugging output: modifies 'directory', e.g. "install" directory

    verb: debugging output: what we're doing to configurations, e.g.
    "packaging" configurations x, y and z

    dir_from_config: callable(configuration): when deriving directories from
    build configurations (at present, unless args.select_dir is specified),
    call this to obtain the directory for the passed build configuration.
    Example: lambda cnf: config.get_build_directory(cnf, args.platform)

    platform: platform on which all this should operate.
        - If platform= is passed, use that platform.
        - If not, but args.platform exists, use that platform.
        - Otherwise use get_current_platform().
    """
    if args.select_dir:
        logger.debug("specified %s directory: %s" % (desc, args.select_dir))
        return [args.select_dir]

    return [dir_from_config(conf)
            for conf in select_configurations(args, config, verb, platform)]


def select_configurations(args, config, verb, platform=None):
    """
    Several of our subcommands provide the ability to specify an individual
    build configuration on which to operate, or several specified
    configurations, or --all configurations. Factor out the common selection
    logic.

    Returns: possibly-empty list of configurations on which to operate.

    Pass:

    args: from argparse. Build your argparse subcommand arguments to set at least:
        all: True when --all is specified
        configurations: list of configuration names, e.g. "Debug"

    config: loaded configuration file (from "autobuild.xml")

    verb: debugging output: what we're doing to the selected configurations, e.g.
    "packaging" configurations x, y and z

    platform: platform on which all this should operate.
        - If platform= is passed, use that platform.
        - If not, but args.platform exists, use that platform.
        - Otherwise use get_current_platform().
    """
    if platform is None:
        try:
            platform = args.platform
        except AttributeError:
            platform = get_current_platform()

    if args.all:
        configurations = config.get_all_build_configurations(platform)
    elif args.configurations:
        configurations = [config.get_build_configuration(name, platform)
                          for name in args.configurations]
    else:
        configurations = config.get_default_build_configurations(platform)
    logger.debug("%s configuration(s) %s" % (verb, pprint.pformat(configurations)))
    return configurations


def establish_build_id(build_id_arg):
    """determine and return a build_id based on (in preference order):
       the --id argument, 
       the AUTOBUILD_BUILD_ID environment variable,
       the date
    If we reach the date fallback, a warning is logged
    In addition to returning the id value, this sets the AUTOBUILD_BUILD_ID environment
    variable for any descendent processes so that recursive invocations will have access
    to the same value.
    """

    build_id = None
    if build_id_arg:
        build_id = build_id_arg
    elif 'AUTOBUILD_BUILD_ID' in os.environ:
        build_id = os.environ['AUTOBUILD_BUILD_ID']
    else:
        build_id = time.strftime("%Y%m%d")
        logger.warn("Warning: no --id argument or AUTOBUILD_BUILD_ID environment variable specified\nUsing the date (%s), which may not be unique" % build_id)
    os.environ['AUTOBUILD_BUILD_ID'] = build_id
    return build_id

######################################################################
#
#   Private module classes and functions below here.
#
######################################################################


def __extract_tar_file(cachename, install_dir, exclude=[]):
    # Attempt to extract the package from the install cache
    tar = tarfile.open(cachename, 'r')
    extract = [member for member in tar.getmembers() if member.name not in exclude]
    conflicts = [member.name for member in extract 
                 if os.path.exists(os.path.join(install_dir, member.name))
                 and not os.path.isdir(os.path.join(install_dir, member.name))]
    if conflicts:
        raise AutobuildError("conflicting files:\n  "+'\n  '.join(conflicts))
    tar.extractall(path=install_dir, members=extract)
    return [member.name for member in extract]


def __extract_zip_archive(cachename, install_dir, exclude=[]):
    zip_archive = zipfile.ZipFile(cachename, 'r')
    extract = [member for member in zip_archive.namelist() if member not in exclude]
    conflicts = [member for member in extract 
                 if os.path.exists(os.path.join(install_dir, member))
                 and not os.path.isdir(os.path.join(install_dir, member))]
    if conflicts:
        raise AutobuildError("conflicting files:\n  "+'\n  '.join(conflicts))
    zip_archive.extractall(path=install_dir, members=extract)
    return extract


class __SCPOrHTTPHandler(urllib2.BaseHandler):
    """
    Evil hack to allow both the build system and developers consume
    proprietary binaries.
    To use http, export the environment variable:
    INSTALL_USE_HTTP_FOR_SCP=true
    """
    def __init__(self, scp_binary):
        self._scp = scp_binary
        self._dir = None

    def scp_open(self, request):
        #scp:codex.lindenlab.com:/local/share/install_pkgs/package.tar.bz2
        remote = request.get_full_url()[4:]
        if os.getenv('INSTALL_USE_HTTP_FOR_SCP', None) == 'true':
            return self.do_http(remote)
        try:
            return self.do_scp(remote)
        except:
            self.cleanup()
            raise

    def do_http(self, remote):
        url = remote.split(':', 1)
        if not url[1].startswith('/'):
            # in case it's in a homedir or something
            url.insert(1, '/')
        url.insert(0, "http://")
        url = ''.join(url)
        logger.info("using HTTP: " + url)
        return urllib2.urlopen(url)

    def do_scp(self, remote):
        if not self._dir:
            self._dir = tempfile.mkdtemp()
        local = os.path.join(self._dir, remote.split('/')[-1])
        if not self._scp:
            raise AutobuildError("no scp command available; cannot fetch %s" % remote)
        command = (self._scp, remote, local)
        logger.info("using SCP: " + remote)
        rv = subprocess.call(command)
        if rv != 0:
            raise AutobuildError("cannot fetch %s" % remote)
        return file(local, 'rb')

    def cleanup(self):
        if self._dir:
            shutil.rmtree(self._dir)

