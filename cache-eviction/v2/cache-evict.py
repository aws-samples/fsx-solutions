#! /usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import argparse
import os
#import boto3
#import boto.utils
#from botocore.exceptions import ClientError
import subprocess
import logging
from datetime import datetime
import multiprocessing
from multiprocessing import Value
import logging.handlers
from subprocess import run

#LOG_FILENAME="/fsx/cache-eviction/cache-eviction.log"
LOG_FILENAME="/home/ec2-user/cache-eviction.log"

def parseArguments():

        parser = argparse.ArgumentParser(description='Cache Eviction script to release least recently accessed files when FSx for Lustre file system free capacity Alarm is triggered')
        parser.add_argument_group('Required arguments')
        parser.add_argument(
                                '-mountpath', required=True, help='Please specify the FSx for Lustre file system mount path')
        parser.add_argument(
                                '-minage', required=True,
                                help='Please specify number of days since last access. Files not accessed for more than this number of days will be considered for hsm release')
        args = parser.parse_args()
        return(args)

def getFileList(fileQueue,mountPath,queue):
    #rank = multiprocessing.current_process()._identity[0]
    #print("In function getFileList: I  am on processor:", rank)

    print('Starting getFileList process => {}'.format(os.getpid()))

    worker_configurer(queue)
    logger=logging.getLogger('getFileList')

    try:
        logger.info("Starting scan for directory path %s",mountPath)
        for root,directories,files in os.walk(mountPath,topdown=False):
            for name in files:
                fileQueue.put(os.path.join(root, name))
        logger.info("Total files scanned: %s", fileQueue.qsize())
    except Exception as e:
        logger.error("Caught Exception. Error is: %s", e)
    fileQueue.put(None)

def checkFileAge(fileQueue,hsmQueue,minage,queue,eligiblefiles,maxHsmQueueProcesses):
    #rank = multiprocessing.current_process()._identity[0]
    #print("In function checkFileAge: I am on processor:", rank)

    print('Starting checkFileAge process => {}'.format(os.getpid()))

    worker_configurer(queue)
    logger=logging.getLogger('checkFileAge')

    today=datetime.today()
    while True:
        try:
            file=fileQueue.get()
            if file is None:
                for i in range(maxHsmQueueProcesses):
                    hsmQueue.put(None)
                break
            #print("Working on file:", name)
            atime=os.stat(file).st_atime
            #print("Access time for file is:", datetime.fromtimestamp(atime))
            fileage=datetime.fromtimestamp(atime)-today
            #print("FileAge is:", fileage)
            fileSize=int(os.stat(file).st_size)
            if int(fileage.days) <= -abs(int(minage)) and fileSize  > 4096:
                hsmQueue.put(file)
                eligiblefiles.value+=1
                logger.info("Adding file %s with access time more  than %s days to the HSM queue",file, fileage.days)
            else:
                logger.info("Ignoring file %s as it is has been accessed in less than %s days or because size of file was %s", file,fileage.days,fileSize)
        except Exception as e:
            logger.error("Caught Exception. Error is: %s", e)
    print("file Queue is empty")


def getHsmState(hsmQueue,releaseQueue,queue,validhsmfiles):
    #rank = multiprocessing.current_process()._identity[0]
    #print("In function getHsmState I am on processor:", rank)

    print('Starting getHSMState process  => {}'.format(os.getpid()))

    worker_configurer(queue)
    logger=logging.getLogger('getHsmState')

    while True:
        try:
            file=hsmQueue.get()
            if file is None:
                break
            cmd = "sudo lfs hsm_state "+file
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, universal_newlines=True)
            (output,error)=p.communicate()
            if error !="":
                logger.error("failed to execute hsm_state on file : %s, error code is: %s",file, error)
            else:
                #print("hsm state of file is:", output)
                if "exists archived"  in output and "released" not in output and "dirty" not in output:
                    validhsmfiles.value+=1
                    releaseQueue.put(file)
                    logger.info("Adding file %s to release queue:", file)
                else:
                    logger.info("Skipping adding file %s to release queue due to hsm state:", file)
        except Exception as e:
            logger.error("Caught Exception. Error is: %s", e)
    releaseQueue.put(None)
    print("HSM Queue is empty")


def releaseFiles(releaseQueue,queue,releasedfiles):
    #rank = multiprocessing.current_process()._identity[0]
    #print("In function releaseFiles I am on processor:", rank)

    print('Starting releaseFiles thread  => {}'.format(os.getpid()))

    worker_configurer(queue)
    logger=logging.getLogger('releaseFiles')

    while True:
        try:
            file=releaseQueue.get()
            if file is None:
                break
            cmd = "sudo lfs hsm_release "+file
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, universal_newlines=True)
            (output,error)=p.communicate()
            if error !="":
                logger.error("failed to execute hsm_release on file : %s, error code is: %s",file, error)
            else:
                logger.info("Initiated HSM release on file: %s",file)
                releasedfiles.value+=1
        except Exception as e:
            logger.error("Caught Exception. Error is: %s", e)
    print("Release Queue is empty")


def listener_process(queue):
    listener_configurer()
    while True:
        record=queue.get()
        if record is None:
            break
        logger=logging.getLogger(record.name)
        logger.handle(record)

def listener_configurer():
    root = logging.getLogger()
    file_handler = logging.handlers.RotatingFileHandler(LOG_FILENAME, 'a', 102400000, 10)
    formatter = logging.Formatter('%(asctime)s %(processName)-10s %(name)s %(levelname)-8s %(message)s')
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    root.setLevel(logging.DEBUG)

def worker_configurer(queue):
    h = logging.handlers.QueueHandler(queue) 
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.DEBUG)


def  mainLog(queue,filesMinAge,eligiblefiles,validhsmfiles,releasedfiles):
        worker_configurer(queue)
        logger=logging.getLogger('Main Process')
        logger.info("Total files not accessed for %s days is %s", filesMinAge, eligiblefiles.value)
        logger.info("Total files in exists archived HSM state are  %s", validhsmfiles.value)
        logger.info("Total files released are  %s", releasedfiles.value)
        return()
##############################################################################
# Main function
##############################################################################
def main():

        eligiblefiles=Value('i',0)
        validhsmfiles=Value('i',0)
        releasedfiles=Value('i',0)

        args = parseArguments()
        
        mountPath=args.mountpath
        filesMinAge=args.minage

        queue=multiprocessing.Queue(-1)
        listener=multiprocessing.Process(target=listener_process, args=(queue,))
        listener.start()

        cpuCount=multiprocessing.cpu_count()
        maxReleaseProcesses=int(((cpuCount/2)-2)/2)
        maxHsmQueueProcesses=int(((cpuCount/2)-2)/2)

        # Build queues. fileQueue to add files from directory search, hsmQueue to add files not accessed in filesMinAge days, releaseQueue to add files that are in exists archived state and eligible for hsm release. 
        fileQueue=multiprocessing.Queue()
 
        manager=multiprocessing.Manager()
        releaseQueue=manager.Queue()
        hsmQueue=manager.Queue()

        # Start Process to scan the input mount or directory path
        fileListProcess=multiprocessing.Process(target=getFileList,args=(fileQueue,mountPath,queue))
        fileListProcess.start()

        # Start Process to work on  files in fileQueue and validate atime and size.
        fileAgeProcess=multiprocessing.Process(target=checkFileAge,args=(fileQueue,hsmQueue,filesMinAge,queue,eligiblefiles,maxHsmQueueProcesses))
        fileAgeProcess.start()

        # Start Process to fetch files from hsmQueue, validate hsm_state and add to releaseQueue
        getHsmStateProcess=[multiprocessing.Process(target=getHsmState, args=(hsmQueue,releaseQueue,queue,validhsmfiles)) for i in range(maxHsmQueueProcesses)]
        for h in getHsmStateProcess:
            h.start()

        # Start Process to initiate hsm_release for files in releaseQueue
        releaseHsmStateProcess=[multiprocessing.Process(target=releaseFiles,args=(releaseQueue,queue,releasedfiles)) for i in range(maxReleaseProcesses)]
        for p in releaseHsmStateProcess:
            p.start()


        for h in getHsmStateProcess:
            h.join()

        for p in releaseHsmStateProcess:
            p.join()

        fileListProcess.join()
        fileAgeProcess.join()

        fileQueue.close()

        mainLog(queue,filesMinAge,eligiblefiles,validhsmfiles,releasedfiles)

        queue.put(None)
        listener.join()

##############################################################################
# Run from command line
##############################################################################
if __name__ == '__main__':
        main()


