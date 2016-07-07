
from __future__ import unicode_literals

import argparse
import errno
import io
import logging
import re
import stat
import subprocess
import sys
import warnings

import portage
portage.proxy.lazyimport.lazyimport(globals(),
        'portage.checksum:hashfunc_map,perform_multiple_checksums,' + \
                'verify_all,_apply_hash_filter,_filter_unaccelarated_hashes',
        'portage.repository.config:_find_invalid_path_char',
        'portage.util:write_atomic,writemsg_level',
)

from portage import os
from portage import _encodings
from portage import _unicode_decode
from portage import _unicode_encode
import manifest
from portage.exception import DigestException, FileNotFound, \
        InvalidDataType, MissingParameter, PermissionDenied, \
        PortageException, PortagePackageException
from const import (MANIFEST1_HASH_FUNCTIONS, MANIFEST2_HASH_DEFAULTS,
        MANIFEST2_HASH_FUNCTIONS, MANIFEST2_IDENTIFIERS, MANIFEST2_REQUIRED_HASH)
from portage.localization import _

if sys.hexversion >= 0x3000000:
	# pylint: disable=W0622
	_unicode = str
	basestring = str
else:
	_unicode = unicode

def guessManifestFileType(filename):
        """ Perform a best effort guess of which type the given filename is, avoid using this if possible """
        if filename.startswith("files" + os.sep + "digest-"):
                return None
        elif filename.startswith("files" + os.sep):
                return "AUX"
        elif filename.endswith(".ebuild"):
                return "EBUILD"
        elif filename == "Manifest" or filename == "MetaManifest":
                return "MANIFEST"
        elif filename.endswith(".eclass"):
                return "ECLASS"
        elif filename in ["ChangeLog", "metadata.xml"]:
                return "MISC"
        elif filename.endswith(".sh"):
	        return "EXEC"
        else:
                return "OTHER"

class MetaManifest(manifest.Manifest):

	def getFullname(self):
                """ Returns the absolute path to the Manifest file for this instance """
                return os.path.join(self.pkgdir, "MetaManifest")

	def sign(self):
		'''Signs MetaManifest file with the default PORTAGE_GPG_KEY''' 
		filename = self.getFullname()
		portage_settings = portage.config(clone=portage.settings)
		portage_portdbapi = portage.portdbapi(portage_settings)
		gpgcmd = portage_settings.get("PORTAGE_GPG_SIGNING_COMMAND")
		if gpgcmd in [None, '']:
			raise portage.exception.MissingParameter(
				"PORTAGE_GPG_SIGNING_COMMAND is unset! Is make.globals missing?")
		if "${PORTAGE_GPG_KEY}" in gpgcmd and "PORTAGE_GPG_KEY" not in portage_settings:
			raise portage.exception.MissingParameter("PORTAGE_GPG_KEY is unset!")
		if "${PORTAGE_GPG_DIR}" in gpgcmd:
			if "PORTAGE_GPG_DIR" not in portage_settings:
				portage_settings["PORTAGE_GPG_DIR"] = os.path.expanduser("~/.gnupg")
			else:
				portage_settings["PORTAGE_GPG_DIR"] = os.path.expanduser(portage_settings["PORTAGE_GPG_DIR"])
			if not os.access(portage_settings["PORTAGE_GPG_DIR"], os.X_OK):
				raise portage.exception.InvalidLocation(
					"Unable to access directory: PORTAGE_GPG_DIR='%s'" %
					portage_settings["PORTAGE_GPG_DIR"])
		gpgvars = {"FILE": filename}
		for k in ("PORTAGE_GPG_DIR", "PORTAGE_GPG_KEY"):
			v = portage_settings.get(k)
			if v is not None:
				gpgvars[k] = v
		gpgcmd = portage.util.varexpand(gpgcmd, mydict=gpgvars)
		gpgcmd = portage.util.shlex_split(gpgcmd)
		gpgcmd = [portage._unicode_encode(arg, encoding=portage._encodings['fs'], errors='strict') for arg in gpgcmd]
		return_code = subprocess.call(gpgcmd)
		if return_code == os.EX_OK:
			os.rename(filename + ".asc", filename)
		else:
			raise portage.exception.PortageException("!!! gpg exited with '" + str(return_code) + "' status")

	def create(self):
		'''Creates a MetaManifest file'''
		dir = self.pkgdir
		if dir.endswith("profiles/"):
			self.create_profile()
		elif dir.endswith("eclass/"):
			self.create_eclass()
		else:
			self.create_cat()

	def create_eclass(self):
		'''Creates a MetaManifest file in the eclass directory'''
		eclass_dir = self.pkgdir
		self.fhashdict = {}
		for ftype in MANIFEST2_IDENTIFIERS:
			self.fhashdict[ftype] = {}

		for eclass_dir, eclassdir_dir, files in os.walk(eclass_dir):
			print(eclass_dir, eclassdir_dir, files)
			for f in files:
				try:
                                        f = _unicode_decode(f,encoding=_encodings['fs'], errors='strict')
                                        eclass_dir = _unicode_decode(eclass_dir,encoding=_encodings['fs'], errors='strict')
				except UnicodeDecodeError:
                        	        continue
				fpath = os.path.join(eclass_dir, f)
				ftype = guessManifestFileType(fpath)
				f = fpath.replace(self.pkgdir, "")
				if not f.endswith("MetaManifest"):
					self.fhashdict[ftype][f] = perform_multiple_checksums(fpath, self.hashes)
		print(self.fhashdict, 10)

	def create_cat(self):
		'''Creates a MetaManifest file in the selected category'''
		catdir = self.pkgdir
		self.fhashdict = {}
		self.fhashdict["MANIFEST"] = {}
		for catdir, catdir_dir, pkg_files in os.walk(catdir):
			for f in pkg_files:
				try:
					f = _unicode_decode(f,encoding=_encodings['fs'], errors='strict')
					catdir = _unicode_decode(catdir,encoding=_encodings['fs'], errors='strict')
				except UnicodeDecodeError:
					continue
				if f == "Manifest" :
					fpath = os.path.join(catdir, f)
					f = fpath.replace(self.pkgdir, "")
					self.fhashdict["MANIFEST"][f] = perform_multiple_checksums(fpath, self.hashes) 
	def create_profile(self):
		'''Creates a MetaManifest file in the profiles directory'''
		profiledir = self.pkgdir
		for ftype in MANIFEST2_IDENTIFIERS:
                        self.fhashdict[ftype] = {}
		for profiledir, profiledir_dir, files in os.walk(profiledir):
			for f in files:
				try:
					f = _unicode_decode(f,encoding=_encodings['fs'], errors='strict')
					profiledir = _unicode_decode(profiledir,encoding=_encodings['fs'], errors='strict')
				except UnicodeDecodeError:
					continue
				fpath = os.path.join(profiledir, f)
				ftype = guessManifestFileType(fpath)
				if ftype == "OTHER":
					ftype = "DATA"
				f = fpath.replace(self.pkgdir, "")
				if not f.endswith("MetaManifest"):
					self.fhashdict[ftype][f] = perform_multiple_checksums(fpath, self.hashes)



if __name__ == '__main__':
	try:

		parser = argparse.ArgumentParser(description='Process some integers.')
		parser.add_argument('directory', metavar='d', type=str,
			help='the selected directory')
		args = parser.parse_args()
		meta_manifest = MetaManifest(args.directory)
		meta_manifest.create()
		meta_manifest.write(sign=True)

	except KeyboardInterrupt:
		print('interrupted ...', file=sys.stderr)
		exit(1)


