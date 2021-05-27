#!/usr/bin/env python3

"""
Pre-requisite:
    sudo python3 -m pip install pyyaml

Because FSx Lustre file system is transactional, file system attribute changes like
DataCompressionType only applies to new writes. To apply these attributes change to
existing files, use this script to rewrite all Lustre regular files at a given
path via "lfs migrate" command.

Depends on the file system size and the amount of data stored, this script can take
a long time to complete. It is generally recommended to:
* Run this script while normal workloads are paused.
* Run this script in a screen session.
* Use guided divide-and-conquer approach if feasible.
    * Migrate one directory at a time. This can be achieved by
        specifying the --migrate-path flag followed by the target
        sub-directory name.
    * Specify a manifest file to list the file paths you would
        like to rewrite. This can be achieved by specifying the
        --manifest-input-path flag followed by your manifest file
        path.

Usage:
  fsx_lustre_migrate_files.py [-h]
                      [--migrate-path MIGRATE_PATH]
                      [--concurrency CONCURRENCY]
                      [--manifest-input-path MANIFEST_INPUT_PATH]
                      [--manifest-output-path MANIFEST_OUTPUT_PATH]

Optional arguments:
  -h, --help            show this help message and exit
  --migrate-path MIGRATE_PATH
                        Path to directory where files will be rewritten with
                        lfs migrate command. Must be a path under Lustre mount
                        point. Default to be /fsx
  --concurrency CONCURRENCY
                        Number of concurrent threads to issue lfs migrate.
                        Default to be the number of CPU cores.
  --manifest-input-path MANIFEST_INPUT_PATH
                        Optional input path to a manifest file that contains a
                        list of file paths that the script will migrate.
  --manifest-output-path MANIFEST_OUTPUT_PATH
                        Override the path where the script writes the file
                        list under given migrate-path. By default the script
                        writes to a temp file at path
                        /tmp/lfs_migrate_manifest.TIMESTAMP.
"""

import argparse
import concurrent.futures
import logging
import multiprocessing
import os
import sys
import subprocess
import time
import traceback
import yaml

from subprocess import DEVNULL, PIPE, STDOUT

DEFAULT_MOUNT_POINT = "/fsx"
DEFAULT_CONCURRENCY = multiprocessing.cpu_count()
DEFAULT_MANIFEST_OUTPUT = "/tmp/lfs_migrate_manifest." + str(time.time())

# Logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def validate_lfs_path(root_path):
    cmd = ["/usr/bin/lfs", "path2fid", root_path]
    p = subprocess.run(cmd, stdout=DEVNULL, stderr=DEVNULL)
    if p.returncode > 0:
        logger.error("Path specified is not a valid path under Lustre mount point: %s", root_path)
        sys.exit(p.returncode)

def list_files(root_path, manifest_output):
    logger.info("Starting recursive tree walk at path: %s", root_path)
    logger.info("Writing Lustre file paths to manifest file at: %s", manifest_output)
    manifest_fd = open(manifest_output, 'w')
    try:
        for root, dirs, files in os.walk(root_path):
            for filename in files:
                manifest_fd.write(os.path.join(root, filename) + "\n")
    finally:
        manifest_fd.close()

def parse_stripe_configuration(file_path):
    cmd = ["/usr/bin/lfs", "getstripe", "-vy", file_path]
    p = subprocess.run(cmd, stdout=PIPE, stderr=STDOUT)

    if "no stripe info" in str(p.stdout):
        # symlink file
        logger.warning("Failed to fetch stripe configuration for file: %s. This may be a symlink file. Skipping",
            file_path)
        sys.exit()

    stripe_conf = yaml.safe_load(p.stdout)
    if not stripe_conf.get("composite_header"):
        is_PFL = False
        # non-PFL, regular stripe configuration
        return is_PFL, {
                "lmm_stripe_count": stripe_conf.get("lmm_stripe_count"),
                "lmm_stripe_size": stripe_conf.get("lmm_stripe_size")
        }

    # PFL layout
    is_PFL = True
    stripe_conf = stripe_conf.get("composite_header")
    lcm_entry_count = int(stripe_conf.get("lcm_entry_count"))

    stripe_configuration = []
    for i in range(lcm_entry_count):
        component_key = "component{}".format(i)
        component_object = stripe_conf.get(component_key)
        start_bytes = component_object.get("lcme_extent.e_start")
        end_bytes = component_object.get("lcme_extent.e_end")
        if end_bytes == "EOF":
            end_bytes = "-1"
        sub_layout = component_object.get("sub_layout")
        lmm_stripe_count = sub_layout.get("lmm_stripe_count")
        lmm_stripe_size = sub_layout.get("lmm_stripe_size")
        ost_start = sub_layout.get("lmm_stripe_offset")
        fid = sub_layout.get("lmm_fid")

        stripe_configuration.append({
            "lmm_stripe_count": lmm_stripe_count,
            "lmm_stripe_size": lmm_stripe_size,
            "start_bytes": start_bytes,
            "end_bytes": end_bytes
        })
    return is_PFL, stripe_configuration

def lfs_migrate(file_path):
    cmd = ["/usr/bin/lfs", "migrate"]
    is_PFL, stripe_configuration = parse_stripe_configuration(file_path)
    if is_PFL:
        for extend_configuration in stripe_configuration:
            cmd.append("-E")
            cmd.append(str(extend_configuration["end_bytes"]))
            cmd.append("-c")
            cmd.append(str(extend_configuration["lmm_stripe_count"]))
            cmd.append("-S")
            cmd.append(str(extend_configuration["lmm_stripe_size"]))
    else:
        cmd.append("-c")
        cmd.append(str(stripe_configuration["lmm_stripe_count"]))
        cmd.append("-S")
        cmd.append(str(stripe_configuration["lmm_stripe_size"]))
    cmd.append(file_path)
    p = subprocess.run(cmd, stdout=DEVNULL, stderr=PIPE)
    if p.returncode > 0:
        logger.error("lfs migrate failed for file: " + file_path)
        logger.error(p.stderr)
        sys.exit(p.returncode)
    logger.info("lfs migrate completed for file: %s", file_path)

def lfs_migrate_at_path(args):
    validate_lfs_path(args.migrate_path)
    if not args.manifest_input_path:
        list_files(args.migrate_path, args.manifest_output_path)
        args.manifest_input_path = args.manifest_output_path

    manifest_fd = open(args.manifest_input_path, 'r')
    file_paths = []
    for file_path in manifest_fd.readlines():
        file_paths.append(file_path.strip())
    manifest_fd.close()

    logger.info("Starting %d threads to migrate files.", args.concurrency)
    with concurrent.futures.ThreadPoolExecutor(max_workers = args.concurrency) as executor:
        futures = []
        for file_path in file_paths:
            futures.append(
                executor.submit(
                    lfs_migrate, file_path
                )
            )
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res:
                    # Expect None as there is no return in the thread impl function.
                    logger.warning(future.result())
            except Exception as e:
                traceback.print_exc()
                logging.error(e)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--migrate-path", help="Path to directory where files will be rewritten with lfs migrate command. Must be a path under Lustre mount point. Default to be /fsx",
                        default=DEFAULT_MOUNT_POINT)
    parser.add_argument("--concurrency", help="Number of concurrent threads to issue lfs migrate. Default to be the number of CPU cores.",
                        default=DEFAULT_CONCURRENCY)
    parser.add_argument("--manifest-input-path", help="Optional input path to a manifest file that contains a list of file paths that the script will migrate.")
    parser.add_argument("--manifest-output-path", help="Override the path where the script writes the file list under given migrate-path. By default the script writes to a temp file at path /tmp/lfs_migrate_manifest.TIMESTAMP.",
                        default=DEFAULT_MANIFEST_OUTPUT)
    args = parser.parse_args()

    # Logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S %Z')
    logger.info("Using migrate path: %s", args.migrate_path)
    logger.info("Using concurrency: %d", args.concurrency)
    if args.manifest_input_path:
        logger.info("Using manifest input path: %s", args.manifest_input_path)
        if not os.path.isfile(args.manifest_input_path):
            sys.exit("Input file path does not exist: " + args.manifest_input_path)
    else:
        # --manifest-output-path is ignored if --manifest-input-path is specified
        logger.info("Using manifest output path: %s", args.manifest_output_path)

    lfs_migrate_at_path(args)

if __name__ == "__main__":
    sys.exit(main())
