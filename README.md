# playgrounds-airflow

a sample docker-compose stack for testing Airflow locally

this attempts to mimic an AWS MWAA environment to minimize change between playground/production

## Installation

assumes that AWS API credentials are configured in your host directory's ~/.aws/credentials

note that you must define a region, otherwise some of the DAG operators throw errors

first initialize the base airflow image

```bash
docker-compose up airflow-init
```

the bring up the rest of the stack
```bash
docker-compose up -d
```
More detailed documentation is available from the [Airflow project](https://airflow.apache.org/docs/apache-airflow/stable/start/docker.html) 

## Custom Images

Start with `apache/airflow:{version}-{py.version}` as a starting point in the Dockerfile and load it with custom stuff


## License
[MIT](https://choosealicense.com/licenses/mit/)