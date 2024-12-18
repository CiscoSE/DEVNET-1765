# Splunk UCC Add-ons

## Preparing Splunk UCC framework

```
apt install python3-venv                            # 0. Install Python venv and pip
python3 -m venv .venv                               # 1. Create a new Python virtual environment
source .venv/bin/activate                           # 2. Change into Python virtual environment
pip install splunk-add-on-ucc-framework             # 3. Install Splunk UCC framework
```

## Building Splunk UCC add-on

```
source .venv/bin/activate                           # 1. Change into Python virtual environment
cd cisco_cnc_addon                                  # 2. Change into add-on directory
ucc-gen build --ta-version "0.0.1"                  # 3. Build add-on with given version number
ucc-gen package --path ./output/cisco_cnc_addon     # 3. Package add-on for installation (.tar.gz)
```

The resulting .tar.gz file, e.g. cisco_cnc_addon-0.0.1.tar.gz can then be installed in Splunk under Manage Apps -> Install app from file.

## More Splunk UCC resources
* https://splunk.github.io/addonfactory-ucc-generator/
* https://conf.splunk.com/files/2024/slides/DEV1885B.pdf
