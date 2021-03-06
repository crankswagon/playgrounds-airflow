from datetime import timedelta
from datetime import datetime
from dateutil.relativedelta import relativedelta
from textwrap import dedent

# The DAG object; we'll need this to instantiate a DAG
from airflow import DAG
#import bs4
#import s3fs
import boto3
import json
# Operators; we need this to operate!
from airflow.operators.bash_operator import BashOperator
from airflow.utils.dates import days_ago
from airflow.providers.amazon.aws.operators.s3_delete_objects import S3DeleteObjectsOperator
from airflow.contrib.operators.aws_athena_operator import AWSAthenaOperator
from airflow.operators.python_operator import PythonOperator

# These args will get passed on to each operator
# You can override them on a per-task basis during operator initialization

exe_t = days_ago(1) ## for backdating, anchor this on a previous month

## for local testing only
session = boto3.session.Session(profile_name = 'derp-profile', region_name='ap-southeast-2')
s3 = session.client('s3')
lmbd=session.client('lambda')


#s3 = boto3.client('s3')
#lmbd = boto3.client('lambda')



"""
for manual local tests, need to also configure an AWS connection, in this case, it is aws-os-prod
add the following JSON to extras for that connection profile: 

{
  "session_kwargs": {
    "profile_name": "derp-profile"
  }
}

"""

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email': ['derp@derp.com.au'],
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
    'params':{
        'cm_year': str(exe_t.year),
        'cm_month': str(exe_t.month).zfill(2),
        'lm_year': str((exe_t+relativedelta(months=-1)).year),
        'lm_month': str((exe_t+relativedelta(months=-1)).month).zfill(2)
                }
    # 'queue': 'bash_queue',
    # 'pool': 'backfill',
    # 'priority_weight': 10,
    # 'end_date': datetime(2016, 1, 1),
    # 'wait_for_downstream': False,
    # 'dag': dag,
    # 'sla': timedelta(hours=2),
    # 'execution_timeout': timedelta(seconds=300),
    # 'on_failure_callback': some_function,
    # 'on_success_callback': some_other_function,
    # 'on_retry_callback': another_function,
    # 'sla_miss_callback': yet_another_function,
    # 'trigger_rule': 'all_success'
}


def get_query(qkey):
    """
    load predefined SQL templates from S3
    """
        
    
    
    cmd = s3.get_object(
            Bucket='os-airflow-prod',
            Key='sql/'+qkey
            )['Body'].read().decode('utf-8')
    return cmd

def call_purge_lambda(**kwargs):
    """
    purges a particular directory in S3
    """
      
    resp=lmbd.invoke(
                    FunctionName = 'purge_s3_folder',   #arn:aws:lambda:ap-southeast-2:123456789:function:purge_s3_folder
                    InvocationType='Event',
                    Payload=  json.dumps({
                                'path' : kwargs['path'],
                                'bucket' : kwargs['bucket']
                                })
                    )
    return(resp['StatusCode']) 


def modify_payload(ti,**kwargs):
    """
    modidfy the data generated by the previous athena step
    """
    
    # in this instances it is not required, but we can use the execution ID to stream the output data here
    fetch_athena_exe_id = ti.xcom_pull(key = 'return_value', task_ids = ['generate-payload'])
    print(f'athena execution id: {fetch_athena_exe_id}')


    src = s3.list_objects_v2(
        Bucket = kwargs['bucket'],
        Prefix = kwargs['path']
        )['Contents']

    keys = [c['Key'] for c in src]

    print(keys)

    # copy this into root directoy, but this function can be extended to do whatever operation on the list of objects
    s3.copy_object(
            Bucket  = kwargs['bucket'],
            CopySource = kwargs['bucket'] + '/' + keys[0], #get the first key cause our CTAS previously bin-ed it into 1 bin
            Key = f'optus-report-{str(exe_t.year)[:-2]}{str(exe_t.month).zfill(2)}{str(exe_t.day).zfill(2)}.json.gz'
        )


def modify_athena_pd(ti,**kwargs):
    """
    modidfy the data generated by the previous athena step using pyathena + pandas; this is much easier than doing object operations using the base athena API
    the previous step generated a tmp table which we will query here; theoretically we could just do the execution in this step too but splitting operation into 2 steps gives some flexibility for
    direct object manipulation for smaller workloads

    using pyathena should work for millions of rows of data, limited only by the amount of memory availabe to each task worker (on mw1.small it's 2GB)

    having said that, this is actually the 'wrong way' to use Airflow, have a read of this: https://medium.com/bluecore-engineering/were-all-using-airflow-wrong-and-how-to-fix-it-a56f14cb0753

    """
    #fetch_athena_exe_id = ti.xcom_pull(key='return_value', task_ids=['generate-payload'])
    #fetch_athena_exe_id = 'f77583d2-4f36-45d6-8e79-ad19e55bf319' #test only
    #qr=athena.get_query_results(QueryExecutionId=fetch_athena_exe_id)

    QUERY = "SELECT * FROM tmp.derp"
    df = cursor.execute(QUERY).as_pandas()
    #df.shape[0]
    keys = ['eventName', 'videoId', 'sessionStart', 'sessionEnd', 'sessionDuration', 'userHash', 'billable', 'externalIdentifier']
    df.columns=keys

    outobj={"tenantId":"optus",
            "events" : json.loads(df.to_json(orient='records'))
            }

    output=json.dumps(outobj)
    #print(output)
    manifest_md5=hashlib.md5(output.encode()).hexdigest()
    #print(manifest_md5) #2f8d7cd4d060213fc09ae7fd4196a0cb

    ## put data
    s3.put_object(Bucket= kwargs['bucket'],
                  Key=f'optus-report-{str(exe_t.year)[-2:]}{str(exe_t.month).zfill(2)}{str(exe_t.day).zfill(2)}.json.gz',
                  #Bucket='os-ext-derp-sessions-prod',
                  #Key='test.json.gz',
                  Body=gzip.compress(bytes(output, 'utf-8'))
                  )

    ## put manifest
    s3.put_object(Bucket= kwargs['bucket'],
                  Key=f'optus-report-{str(exe_t.year)[-2:]}{str(exe_t.month).zfill(2)}{str(exe_t.day).zfill(2)}.manifest',
                  #Bucket='os-ext-derp-sessions-prod',
                  #Key='test.manifest',
                  Body=json.dumps({'content_hash':manifest_md5,
                        'hash_lib': 'md5',
                        'generator':'os-airflow',
                        'export_ts': datetime.utcnow().timestamp(),
                        'maintainer':'optussportreportingadmin@optus.com.au'}).encode()
                        )




with DAG(
    'pull_derp_sample_data',
    default_args=default_args,
    description='Pull sample data for derp',
    schedule_interval='@daily',#timedelta(days=1),
    start_date=exe_t,
    tags=['derp']
) as dag:

    echodate  = BashOperator(
        task_id='print_date',
        bash_command='date',
    )

    #t2 = BashOperator(
    #    task_id='derp',
    #    depends_on_past=False,
    #    bash_command='sleep 5',
    #    retries=3,
    #)

    dag.doc_md = __doc__

    echodate.doc_md = dedent(
    """\
    #### Task Documentation
    You can document your task using the attributes `doc_md` (markdown),
    `doc` (plain text), `doc_rst`, `doc_json`, `doc_yaml` which gets
    rendered in the UI's Task Instance Details page.

    ![img](http://montcs.bloomu.edu/~bobmon/Semesters/2012-01/491/import%20soul.png)
    """
    )
    templated_command = dedent(
        """
    {% for i in range(5) %}
        echo "{{ ds }}"
        echo "{{ macros.ds_add(ds, 7)}}"
        echo "{{ params.my_param }}"
    {% endfor %}
    """
    )


    dropTable = AWSAthenaOperator(
        task_id = 'drop-tmp',
        query=get_query('derp/drop-table-derp.sql'), ##templated, parameterise {{}}
        depends_on_past=False,
        database='tmp',
        output_location='s3://aws-athena-query-results-123456789-ap-southeast-2',
        aws_conn_id='aws-os-prod',
        sleep_time=15,
        max_tries=2

        )
    cleanObjStore = PythonOperator(
        task_id = 'clean-up-obj-store',
        provide_context=True,
        python_callable=call_purge_lambda,
        op_kwargs={ 'bucket':'os-ext-derp-sessions-prod', 
                    'path' : 'tmp/' 
                    }
                    )

    createTable = AWSAthenaOperator (
        task_id = 'generate-payload',
        query=get_query('derp/ctas-derp-sessions.sql'), ##templated, parameterise {{}}
        depends_on_past=False,
        database='tmp',
        output_location='s3://aws-athena-query-results-123456789-ap-southeast-2',
        aws_conn_id='aws-os-prod',
        sleep_time=15,
        max_tries=2,
        do_xcom_push=True
        )

    createTable.doc_md = dedent(
    """
    ### Generate 
    We can use the athena engine to create whatever payload we want
    The output dir of this step is defined in the CTAS 'external location' property
    Once we get the object, a subsequent step in the DAG can do further manipulation re: compression/naming etc.

    """
        )


    modifyPayload = PythonOperator(
        task_id = 'modify-payload',
        provide_context=True,
        python_callable=modify_payload,
        op_kwargs={'bucket':'os-ext-derp-sessions-prod', 
                    'path' : 'tmp/' 
                    }
                    )



    ## drop first, so after each DAG run, there is a query-able artifact in athena for you to double check the last payload
    echodate >> dropTable  >> cleanObjStore >> createTable >> modifyPayload