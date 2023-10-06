
# $ wls-analytics 

wls-analytics provides tools to analyse Oracle WebLogic logs.

This is the early version of the tool. It is still under development.

When you clone the repository, you can build the module from source as follows. 

```bash
make build 
```

As a result, the module package will be created under the `dist` directory. You can then install it as follows.

```bash
pip install dist/wls_analytics-{version}-py3-none-any.whl
```

Please change the version of the module accordingly.

Run `wlsanalytics --help` for details how to use the tool. 