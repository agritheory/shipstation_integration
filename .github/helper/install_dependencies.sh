#!/bin/bash

# Check for merge conflicts before proceeding
python -m compileall -f $GITHUB_WORKSPACE
if grep -lr --exclude-dir=node_modules "^<<<<<<< " $GITHUB_WORKSPACE
    then echo "Found merge conflicts"
    exit 1
fi

sudo apt update -y
sudo apt remove mysql-server mysql-client
sudo apt install libcups2-dev redis-server mariadb-client-10.6 -y
