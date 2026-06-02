#!/bin/bash

export PIP_ROOT_USER_ACTION=ignore

set -e

# Check for merge conflicts before proceeding
python -m compileall -f "${GITHUB_WORKSPACE}"
if grep -lr --exclude-dir=node_modules "^<<<<<<< " "${GITHUB_WORKSPACE}"
    then echo "Found merge conflicts"
    exit 1
fi

cd ~ || exit

pip install --upgrade pip
pip install setuptools
pip install frappe-bench

mysql --host 127.0.0.1 --port 3306 -u root -e "SET GLOBAL character_set_server = 'utf8mb4'"
mysql --host 127.0.0.1 --port 3306 -u root -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"

mysql --host 127.0.0.1 --port 3306 -u root -e "CREATE OR REPLACE DATABASE test_site"
mysql --host 127.0.0.1 --port 3306 -u root -e "CREATE OR REPLACE USER 'test_site'@'localhost' IDENTIFIED BY 'test_site'"
mysql --host 127.0.0.1 --port 3306 -u root -e "GRANT ALL PRIVILEGES ON \`test_site\`.* TO 'test_site'@'localhost'"

mysql --host 127.0.0.1 --port 3306 -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED BY 'root'"  # match site_config
mysql --host 127.0.0.1 --port 3306 -u root -e "FLUSH PRIVILEGES"

echo BRANCH_NAME: "${BRANCH_NAME}"

# Helper function to clone with fallback branch
clone_with_fallback() {
    local repo_url=$1
    local target_branch=$2
    local fallback_branch=$3
    local app_name=$(basename "$repo_url" .git)

    if git ls-remote --heads "$repo_url" | grep -q "refs/heads/${target_branch}"; then
        echo "Cloning $app_name with branch ${target_branch}"
        git clone "$repo_url" --branch "${target_branch}"
    elif git ls-remote --heads "$repo_url" | grep -q "refs/heads/${fallback_branch}"; then
        echo "Branch ${target_branch} not found in $app_name, using ${fallback_branch}"
        git clone "$repo_url" --branch "${fallback_branch}"
    else
        echo "Neither ${target_branch} nor ${fallback_branch} found in $app_name, cloning default branch"
        git clone "$repo_url"
    fi
}

# Clone frappe with fallback
clone_with_fallback "https://github.com/frappe/frappe" "${BRANCH_NAME}" "version-15"
bench init frappe-bench --frappe-path ~/frappe --python "$(which python)" --skip-assets --ignore-exist

mkdir ~/frappe-bench/sites/test_site
cp -r "${GITHUB_WORKSPACE}/.github/helper/site_config.json" ~/frappe-bench/sites/test_site/

cd ~/frappe-bench || exit

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile

# Helper function to get app with fallback branch
get_app_with_fallback() {
    local app_name=$1
    local repo_url=$2
    local target_branch=$3
    local fallback_branch=$4

    if git ls-remote --heads "$repo_url" | grep -q "refs/heads/${target_branch}"; then
        echo "Getting $app_name with branch ${target_branch}"
        bench get-app "$app_name" "$repo_url" --branch "${target_branch}" --skip-assets
    elif git ls-remote --heads "$repo_url" | grep -q "refs/heads/${fallback_branch}"; then
        echo "Branch ${target_branch} not found in $app_name, using ${fallback_branch}"
        bench get-app "$app_name" "$repo_url" --branch "${fallback_branch}" --skip-assets
    else
        echo "Neither ${target_branch} nor ${fallback_branch} found in $app_name, using default branch"
        bench get-app "$app_name" "$repo_url" --skip-assets
    fi
}

# Get apps with fallback branches
get_app_with_fallback "erpnext" "https://github.com/frappe/erpnext" "${BRANCH_NAME}" "version-15"
get_app_with_fallback "hrms" "https://github.com/frappe/hrms" "${BRANCH_NAME}" "version-15"
get_app_with_fallback "beam" "https://github.com/agritheory/beam" "${BRANCH_NAME}" "version-15"
get_app_with_fallback "inventory_tools" "https://github.com/agritheory/inventory_tools" "${BRANCH_NAME}" "version-15"

# Get the local app
bench get-app shipstation_integration "${GITHUB_WORKSPACE}" --skip-assets

bench setup requirements --python
# Pin setuptools<82 in bench venv — v82 removed pkg_resources which some packages need
~/frappe-bench/env/bin/pip install 'setuptools<82'
bench use test_site

bench start &> bench_run_logs.txt &
CI=Yes &
bench --site test_site reinstall --yes --admin-password admin

bench setup requirements --dev

echo "BENCH VERSION NUMBERS:"
bench version
echo "SITE LIST-APPS:"
bench list-apps

bench start &> bench_run_logs.txt &
CI=Yes &
bench execute 'shipstation_integration.tests.setup.before_test'
