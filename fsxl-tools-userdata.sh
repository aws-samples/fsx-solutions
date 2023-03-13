#cloud-config
repo_update: true
repo_upgrade: all


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


runcmd:
- amazon-linux-extras install -y epel lustre python3.8
- yum groupinstall -y "Development Tools"
- yum install -y libaio-devel fpart parallel iftop ioping iperf3 hping3 tree nload ncdu nmon python3-devel amazon-efs-utils

# install and configure aws cli v2
- cd /home/ec2-user
- curl "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip"
- unzip awscliv2.zip
- ./aws/install

# install openmpi
- cd /home/ec2-user
- wget https://download.open-mpi.org/release/open-mpi/v4.1/openmpi-4.1.4.tar.gz
- tar xvzf openmpi-4.1.4.tar.gz
- cd openmpi-4.1.4
- ./configure --with-lustre
- make all
- make install

# install ior
- cd /home/ec2-user
- wget https://github.com/hpc/ior/releases/download/3.3.0/ior-3.3.0.tar.gz
- tar xvzf ior-3.3.0.tar.gz
- cd ior-3.3.0
- ./configure --with-lustre
- make all
- make install

# install smallfile
- cd /home/ec2-user
- git clone https://github.com/bengland2/smallfile.git

# install fio
- cd /home/ec2-user
- git clone git://git.kernel.dk/fio.git
- cd fio
- ./configure
- make all
- make install
