
# $ wls-analytics 

wls-analytics provides tools to analyse Oracle WebLogic logs.

This is the early version of the tool. It is still under development.

## Installation

When you clone the repository, you can build the module from source as follows. 

```bash
$ pip install --upgrade setuptools wheel
$ make build 
```

As a result, the module package will be created under the `dist` directory. You can then install it as follows.

```bash
pip install dist/wls_analytics-{version}-py3-none-any.whl
```

Please change the version of the module accordingly.

Run `wlsanalytics --help` for details how to use the tool. 

## Features

There is a command to analyze SOA out logs. You can use it as follows. 

Set the home directory of the tool in the `WLSA_HOME` environment variable.

```bash
$ export WLSA_HOME=/path/to/wls-analytics
   ```

Create the configuration file in `$WLSA_HOME/config/config.yaml` and define the sets of log files the tool should analyze. Please check [`config/config.yaml`](https://github.com/tomvit/wls-analytics/blob/main/config/config.yaml) for the sample configuration file.

In the same configuration file, define the patterns to be searched in logs. There is already a number of patterns defined in the file. You can add your own patterns as well.

Define the location of the configuration file in the `WLSA_CONFIG` environment variable. Alternatively, you can provide the path to the configuration file as an argument to the command.

```bash
$ export WLSA_CONFIG=$WLSA_HOME/config/config.yaml
```

Run the command to analyze the set of log files called `o2c`. The set must be defined in the configuration file.

```bash
$ wlsanalytics soa error o2c --from "2023-09-19 11:06:39" --to "2023-09-19 12:06:39"
```

There are various configuration options you can use. Run `wlsanalytics soa error --help` for details.

As a result, you will get the following output:

```bash
-- Time range: 2023-09-19 11:06:39 - 2023-09-19 12:06:39
-- Searching files in the set 'o2c'
-- Reading entries from 2 files: 83.6MB [00:07, 12.4MB/s]
-- Completed in 9.43s
TIME                        SERVER       FLOW_ID     COMPOSITE                            ERROR                  DETAIL
2023-09-19 11:06:51.875000  soa_server1  1293508714  SyncCustomerPartyListBRMCommsProvA…  ERR_VALIDATION_FAILED  PCM_OP_UPDATE_CUSTOMER
2023-09-19 11:07:33.145000  soa_server1  1293447650  SyncCustomerPartyListBRMCommsProvA…  ERR_VALIDATION_FAILED  PCM_OP_UPDATE_CUSTOMER
2023-09-19 11:08:02.565000  soa_server1  1293525804  SyncCustomerPartyListBRMCommsProvA…  ERR_VALIDATION_FAILED  PCM_OP_UPDATE_CUSTOMER
...
```
    
When you use the index option `--index`, there will be an index created for the output. You can then use to explore log entries that belong to specifc error by using the index value.    

```bash
wlsanalytics soa index tevu
```

This will open the `less` viewer on your local computer with the entries that belong to the error with the index value `tevu`.


