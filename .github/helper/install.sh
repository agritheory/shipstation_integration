#!/bin/bash

export PIP_ROOT_USER_ACTION=ignore

set -e
cd ~ || exit

pip install --upgrade pip
pip install frappe-bench

mysql --host 127.0.0.1 --port 3306 -u root -e "SET GLOBAL character_set_server = 'utf8mb4'"
mysql --host 127.0.0.1 --port 3306 -u root -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"
mysql --host 127.0.0.1 --port 3306 -u root -e "CREATE OR REPLACE DATABASE test_site"
mysql --host 127.0.0.1 --port 3306 -u root -e "CREATE OR REPLACE USER 'test_site'@'localhost' IDENTIFIED BY 'test_site'"
mysql --host 127.0.0.1 --port 3306 -u root -e "GRANT ALL PRIVILEGES ON \`test_site\`.* TO 'test_site'@'localhost'"
mysql --host 127.0.0.1 --port 3306 -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED BY 'root'"  # match site_config
mysql --host 127.0.0.1 --port 3306 -u root -e "FLUSH PRIVILEGES"

bench init --skip-assets --frappe-branch version-15 --python "$(which python)" frappe-bench

mkdir ~/frappe-bench/sites/test_site
cp -r "${GITHUB_WORKSPACE}/.github/helper/site_config.json" ~/frappe-bench/sites/test_site/

cd ~/frappe-bench || exit

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile

bench get-app erpnext --branch "version-15" --resolve-deps
bench get-app shipstation_integration "${GITHUB_WORKSPACE}"
bench setup requirements --dev
bench use test_site
bench --site test_site reinstall --yes --admin-password admin

bench start &> bench_run_logs.txt &
CI=Yes &
bench execute 'shipstation_integration.tests.setup.before_test'
