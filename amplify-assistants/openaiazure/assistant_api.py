from enum import Enum
import json
import math
import math
import uuid
import time
from functools import reduce
from io import BytesIO
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from common.secrets import get_secret_value
from common.credentials import get_endpoint
import os
from openai import OpenAI
from openai import AzureOpenAI
from datetime import datetime, timezone
from .token import count_tokens
from PIL import Image


openai_provider = os.environ['ASSISTANTS_OPENAI_PROVIDER']
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')

model = "gpt-4o"

def get(dictionary, *keys):
    return reduce(lambda d, key: d.get(key, None) if isinstance(d, dict) else None, keys, dictionary)


def get_openai_client():
    openai_api_key = get_secret_value("OPENAI_API_KEY")
    if openai_provider == "openai":
        openai_api_key = openai_api_key
        client = OpenAI(
            api_key=openai_api_key
        )
        return client
    elif openai_provider == "azure":
        azure_endpoint, azure_api_key = get_endpoint("code-interpreter", os.environ['LLM_ENDPOINTS_SECRETS_NAME'])
        client = AzureOpenAI(
            api_key = azure_api_key,  
            api_version = "2024-05-01-preview",
            azure_endpoint = azure_endpoint
            )
        return client
    return None

# define client 
client = get_openai_client()

def file_keys_to_file_ids(file_keys):
    bucket_name = os.environ['ASSISTANTS_FILES_BUCKET_NAME']

    updated_keys = []
    for file_key in file_keys:
        file_key_user = file_key.split('//')[1]
        if '@' not in file_key_user or len(file_key_user) <= 6:
            return []
        updated_keys.append(file_key_user)
        
    file_ids = []
    for file_key in updated_keys:
        print("Downloading file: {}/{} to transfer to OpenAI".format(bucket_name, file_key))
        # Use a BytesIO buffer to download the file directly into memory
        file_stream = BytesIO()
        s3.download_fileobj(bucket_name, file_key, file_stream)
        file_stream.seek(0)  # Move to the beginning of the file-like object

        print("Uploading file to OpenAI: {}".format(file_key))

        # Create the file on OpenAI using the downloaded data
        response = client.files.create(
            file=file_stream,
            purpose="assistants"
        )

        print("Response: {}".format(response))
        # Here you might want to collect the file IDs into a list
        file_id = response.id
        if file_id:
            file_ids.append(file_id)

        # Important: Close the BytesIO object when done
        file_stream.close()

    return file_ids


def send_file_to_s3(file_content, file_key, file_name, user_id, content_type = 'binary/octet-stream'):
    print("Sending files to s3")
    bucket_name = os.environ['ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME']
    
    try: 
        print("Transfer file to s3 bucket: ".format(bucket_name))
        file_stream = BytesIO(file_content)
        print("File Stream: ", file_stream)
        s3.upload_fileobj(file_stream, bucket_name, file_key, 
                          ExtraArgs={'ACL': 'private','ContentType': content_type})

        print(f"File uploaded to S3 bucket '{bucket_name}' with key '{file_key}'")

        file_url = get_presigned_download_url(file_key, user_id, file_name)
        if (file_url['success']):
            return {"success" : True, "presigned_url": file_url['downloadUrl']}
        return file_url

    except (NoCredentialsError):
        print('Credentials not available')
    except (ClientError) as e:
        # DynamoDB client error handling
        print(f"Failed to upload file to S3")
        print(e.response['Error']['Message'])
    except Exception as e:
        # Handle other possible exceptions
        print(f"An unexpected error occurred: {e}")
    finally:
        file_stream.close()


def create_low_res_version(file):
    print("Creating lower resolution version of image")
    image = Image.open(BytesIO(file.content))
    original_width, original_height = image.size
    target_size_bytes = 204800  # 200KB
    max_width, max_height = 800, 600  # Initial max dimensions
    
    try:
        while True:
            # Calculate the target size while maintaining aspect ratio
            ratio = min(max_width / original_width, max_height / original_height)
            target_size = (int(original_width * ratio), int(original_height * ratio))
            
            resized_image = image.resize(target_size, Image.LANCZOS)
            
            # Save the resized image to a bytes buffer
            resized_bytes = BytesIO()
            resized_image.save(resized_bytes, format=image.format)
            resized_size = resized_bytes.tell()  # Get the resized image size
            
            # Check if the resized image meets the size criteria
            if resized_size <= target_size_bytes:
                break
            
            # Calculate scale factor based on the current size vs. target size
            size_ratio = resized_size / target_size_bytes
            scale_factor = math.sqrt(size_ratio)
            
            # Adjust max_width and max_height based on scale factor for the next attempt
            max_width = int(max_width / scale_factor)
            max_height = int(max_height / scale_factor)

            # Ensure the loop can exit if max dimensions become too small
            if max_width < 100 or max_height < 100:
                raise ValueError("Unable to reduce image size to under the target threshold without making it too small.")

        # Ensure buffer is ready for reading
        resized_bytes.seek(0)  
        return resized_bytes.getvalue()
    finally:
        resized_bytes.close() 


def determine_content_type(file_name):
    print("Determining file type of: ", file_name)
    extension = file_name.split('.')[-1] 
    if (extension == 'csv'):
        return 'text/csv' 
    elif (extension == 'pdf'):
        return 'application/pdf'
    elif (extension == 'png'):
        return 'image/png'
    else:
        return 'binary/octet-stream'



def get_presigned_download_url(key, current_user, download_filename = None):
    s3 = boto3.client('s3')
    bucket_name = os.environ['ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME']
    
    print(f"Getting presigned download URL for {key} for user {current_user}")

    response_headers = {
        'ResponseContentDisposition': f'attachment; filename="{download_filename}"'} if download_filename else {}

    # If the user matches, generate a presigned URL for downloading the file from S3
    try:
        presigned_url = s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={
                'Bucket': bucket_name,
                'Key': key,
                **response_headers
            },
            ExpiresIn=28800  # Expires in 12 hrs 
        )
    except ClientError as e:
        print(f"Error generating presigned download URL: {e}")
        return {'success': False, 'message': "File not found"}

    if presigned_url:
        # print("Successfully retrieved a new presigned url: ", presigned_url)
        return {'success': True, 'downloadUrl': presigned_url}
    else:
        print("Failed to retrieve a new presigned url")
        return {'success': False, 'message': 'File not found' }


def chat_with_code_interpreter(current_user, assistant_id, messages, account_id, request_id):
    print("Entered Chat_with_code_interpreter")
    print("Creating assistant with ", openai_provider)
    assistant_existence = check_assistant_exists(assistant_id, current_user)
    if (not assistant_existence['success']):
        return assistant_existence
    
    azure_assistant_id = assistant_existence['provider_assistant_id']
    print("Initiating chat function")
    return chat(current_user, azure_assistant_id, messages, assistant_id, account_id, request_id)


def get_active_thread_id_for_chat(client, messages, info): 
    print("Initiate getting active thread")
    updated_info, new_messages_to_last_known = get_last_known_thread_id(client, messages, info)

    if (updated_info.get('thread_id') is None):
       return create_new_thread_for_chat(client, messages, info)
    
    updated_last_known_thread = message_catch_up_on_thread(client, new_messages_to_last_known, info)
    if (not updated_last_known_thread['success']):
        return updated_last_known_thread

    return {'success': True, 
            'message': 'Successfully retrieved the last known thread and verified it is active.',
            'data': updated_info}


def check_last_known_thread(client, info):
    thread_id = info['thread_id']
    print("Checking if the last known thread is still good. ThreadId: ", thread_id)
    timestamp = int(time.time() * 1000)
    try:
        thread_info = client.beta.threads.retrieve(thread_id)
        print(thread_info)
        if (thread_info.id):
            op_details = {'type': "RETRIEVE_THREAD", 'timestamp':  timestamp}
            record_thread_usage(op_details, info) # record when you activate the thread
            return {'success': True, 'message': 'Successfully retrieved the last known thread and verified it is active.'}
        
        return {'success': False, 'error': "Last known thread is no good."}
    except Exception as e:
        print(e)
        return {'success': False, 'error': 'Failed to check last known threads status.'}
            

def get_last_known_thread_id(client, messages, info):
    if (len(messages) == 0 ):
        return info, messages
    
    print("Retrieving last known thread and missing messages")
    # traverse backward to see if and when there is some codeiterpreter message data attached to the messages passed in 
    for index in range(len(messages) - 1, -1, -1):
        if ('codeInterpreterMessageData' in messages[index] and messages[index]['codeInterpreterMessageData'] is not None
            and 'threadId' in messages[index]['codeInterpreterMessageData']):
            print("Message with code interpreter message data: ", messages[index]['codeInterpreterMessageData'])
            # theres no way the very last message in the list can have codeInterpreterMessageData according to existing logic
            # the list will always contain the new user prompt, so will always at the bare minimum get messages[-1] if code interpreter has been used at some point
            thread_key = messages[index]['codeInterpreterMessageData']['threadId']
            thread_info = get_thread(thread_key, info['current_user'])
            if (thread_info['success']):
                info['thread_key'] = thread_key
                info['thread_id'] = thread_info['openai_thread_id']
                thread_info = check_last_known_thread(client, info)
                if (thread_info['success']):
                    return info, messages[index + 1:]
    
    return info, messages # occurs when code interpreter hasnt been used in a conversation or thread id wasnt found, we need to create a new one


def create_new_thread_for_chat(client, messages, info):
    user_id = info['current_user']
    print("Creating a new thread")
    dynamodb = boto3.resource('dynamodb')
    threads_table = dynamodb.Table(os.environ['ASSISTANT_THREADS_DYNAMODB_TABLE'])
    timestamp = int(time.time() * 1000)
    thread_key = f'{user_id}/thr/{str(uuid.uuid4())}'

    try:
        # Create a new thread using the OpenAI Client
        print("Creating OpenAI thread...")
        thread_id = client.beta.threads.create().id 

        print("Recording thread usage")
        info['thread_id'] = thread_id
        info['thread_key'] = thread_key
        op_details = {'type': "CREATE_THREAD", 'timestamp': timestamp}
        record_thread_usage(op_details, info) ## record when you create 
        
        print(f"Created thread: {thread_id}")
        message_catch_up_on_thread(client, messages, info)
    except Exception as e:
        print(e)
        return {'success': False, 'error': 'Failed to create new thread with the client.'}  
    
    
    # DynamoDB new item structure for the thread
    new_item = {
        'id': thread_key,
        'data': {openai_provider: {'threadId': thread_id}},
        'user': user_id,
        'createdAt': timestamp,
        'updatedAt': timestamp,
    }
    # Put the new item into the DynamoDB table
    threads_table.put_item(Item=new_item)
    print('Successful creation and sync of messages to new threadId: ', thread_id)
    return {'success': True, 
            'message': 'Successful creation and sync of messages to new thread.',
            'data': info }


def sanitize_messages(messages):
    print("Entered sanitize_mssages")
    sanitized_messages = []
    i = 0
    while i < len(messages):
        message = messages[i]
        content = f"user: {message['content']}" 
        if i + 1 < len(messages): # doesnt support assistant messages so we need to few shot it
            content += f" | assistant: {messages[i+1]['content']}"

        sanitized_message = {
            "role": "user", 
            "content": content,
            "file_ids": []
        }
       
        if (message.get('data') and ('dataSources' in message['data']) and (len(message['data']['dataSources']))):
            file_ids = file_keys_to_file_ids([source['id'] for source in message['data']['dataSources']])
            sanitized_message['file_ids'] = file_ids
            
        sanitized_messages.append(sanitized_message)
        i += 2
    #print("Sanitized messages: ", sanitized_messages)
    return sanitized_messages


# since we dont always using code interpreter in the conversation, there may be messages that the thread
# is missing , so lets add it by combining their content 
# note this will also add the lastest user message
def message_catch_up_on_thread(client, missing_messages, info): ## needs help
    print("Get any missing messages on the thread")
    sanitized_messages = sanitize_messages(missing_messages)
    messages_to_send = []

    current_content = ""
    current_file_ids = []
    for i in range(0, len(sanitized_messages)):
        msg = sanitized_messages[i]
        if (len(current_file_ids) + len(msg['file_ids']) > 10): # we want message content and files to stay together even 
            if current_content: messages_to_send.append({'content': current_content, 'file_ids': current_file_ids})

            if (len(msg['file_ids']) > 10): #for cases where a large sum of messages are added to one message
                current_content = f"The following data sources are in regards to my prompt: {msg['content']}"
                messages_to_send.append({'content': current_content, 'file_ids': msg['file_ids'][:10]})

                current_file_ids = msg['file_ids'][10:]
                while (current_file_ids):
                    if len(current_file_ids) > 10:
                        messages_to_send.append({'content': current_content, 'file_ids': current_file_ids[:10]})
                        current_file_ids = current_file_ids[10:]
                    else: # so all sources pertaining to the same messages are together 
                        #even if there is only one message left over, it still goes with the message that it was attached to.
                        messages_to_send.append({'content': current_content, 'file_ids': current_file_ids})
                        current_content = ''
                        current_file_ids = []
            else: # we can begin the new set of content and file ids 
                current_content = f"{msg['content']}"
                current_file_ids = msg['file_ids']

        else: # we can add and merge with previous messages
            current_content += f"\n{msg['content']}"
            current_file_ids.extend(msg['file_ids'])

    if current_content: messages_to_send.append({'content': current_content, 'file_ids': current_file_ids})

    print("Total messages in to send list: ", len(messages_to_send))
    # print("Messages to send list: ", messages_to_send)

    try:
        print("Adding missing messages to thread.")
        for message in messages_to_send:
            timestamp = int(time.time() * 1000)
            content = message['content']

            messageResponse =  client.beta.threads.messages.create(
                                    thread_id= info['thread_id'],
                                    role= "user",
                                    content= content,
                                    attachments= [{
                                                "file_id": file_id,
                                                "tools": [{"type": "code_interpreter"}]
                                                } for file_id in message['file_ids']
                                                ])
        
            # print(f"Result: {messageResponse}")
             
            op_details = {'type': "ADD_MESSAGE", 'timestamp':  timestamp, 'messageID': messageResponse.id, #would cause an exception if raises a KeyError
                          "inputTokens": count_tokens(content)}
            record_thread_usage(op_details, info) #record every message added 
        return {'success': True, 'message': 'Successfully added missing messages to the thread.'}
    except Exception as e:
        print(e)
    return {'success': False, 'error': 'Failed to sync messages to the thread.'}


def chat(current_user, provider_assistant_id, messages, assistant_key, account_id, request_id):

    info = {
    'assistant_key': assistant_key,
    'assistant_id': provider_assistant_id,
    'current_user': current_user,
    'request_id': request_id,
    'account_id': account_id
    }

    thread_id_data = get_active_thread_id_for_chat(client, messages, info)
    
    if (not thread_id_data['success']):
        return thread_id_data
    
    info = thread_id_data['data']
   
    openai_thread_id = info['thread_id']

    try:
        print(f"Running assistant {provider_assistant_id} on thread {openai_thread_id}")
        run = client.beta.threads.runs.create(
            thread_id=openai_thread_id,
            assistant_id=provider_assistant_id
        )
        # print(f"Run created: {run}")

        tries = 28
        while tries > 0:
            print(f"Checking for the result of the run {run.id}")
            try:
                status = client.beta.threads.runs.retrieve(
                    thread_id=openai_thread_id,
                    run_id=run.id
                )

                print(f"Status {status.status}")
                if status.status == 'completed':
                    break
                elif (status.status in ["failed", "cancelled", "cancelling", "requires_action", "expired", "incomplete"]):
                    print("Error run status: ", status)
                    return {'success': False, 'error': f"Error with run status : {status.status}"} 
            except Exception as e:
                print(e)
                run_data = {openai_provider : {
                'threadId': run.thread_id,
                'runId': run.id,
                'assistantId': run.assistant_id,
                'createdAt': run.created_at,
                "last_error": run.last_error
                }}
                record_thread_run_data(run_data, "Failed", info)
                return {'success': False, 'error': 'Failed to retrieve run status.'} 

            time.sleep(1)
    except Exception as e:
        print(e)
        return {'success': False, 'error': 'Failed to run the assistant on the thread.'}
    
    timestamp = int(time.time() * 1000)
    print(f"Fetching the messages from {openai_thread_id}")
    thread_messages = client.beta.threads.messages.list(thread_id=openai_thread_id)
    # We only care about the last message which is the assistant reply because we are keeping track in our messages 
    print(f"Formatting messages")

    #assitant response message is the first in the data list 
    assistantMessage = thread_messages.data[0]
    print("Assistant Response Message: ", assistantMessage)
    
    # make sure its the assistant response! 
    if (not assistantMessage.role == "assistant"):
        return {'success': False, 'error': "Failed to get assistant response"}
    
    message_id = assistantMessage.id
    run_data = {openai_provider : {
            'messageId': message_id,
            'threadId': assistantMessage.thread_id,
            'runId': assistantMessage.run_id,
            'assistantId': assistantMessage.assistant_id,
            'createdAt': assistantMessage.created_at,
            }, **assistantMessage.metadata
            }
    #put in thread runs table
    record_thread_run_data(run_data, "completed", info)

    responseData = {"data": {'threadId': info['thread_key'],
                            'role': assistantMessage.role,
                            'textContent': ""
                            }
                    }
    # handle formatting content 
    content = []
    for item in assistantMessage.content:
        if item.type == 'text':
            item_data = item.text
            # actual response text to show user 
            responseData['data']['textContent'] += item_data.value + '\n'

            for annotation in item_data.annotations:
                print("Annotation: ", annotation)
                if (annotation.type == "file_path"):
                    print("Code Interpreter generated a file!")
                    created_file_id = annotation.file_path.file_id
                    file_obj = client.files.retrieve(created_file_id)
                    file_name = file_obj.filename[file_obj.filename.rfind('/') + 1:]  
                    s3_file_key = f"s3://{current_user}/{message_id}-{created_file_id}-FN-{file_name}"
                    file_content = client.files.content(file_obj.id)

                    # only csv and pdf are currently supported 
                    content_type = determine_content_type(annotation.text)
                    print("File content type: ", content_type)
                    content_values = get_response_values(file_content, content_type, s3_file_key, current_user, file_name)
                    if (content_values['success']):
                        content.append(content_values['data'])

        elif item.type == 'image_file': 
            # no longer necessary since recent updates 
            print("Code Interpreter generated an image file!")
            continue
            created_file_id = item.image_file.file_id
            #send file to s3 ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME 
            s3_file_key = f"s3://{current_user}/{message_id}-{created_file_id}"
            file_content = client.files.content(created_file_id)

            content_values = get_response_values(file_content, 'image/png', s3_file_key, current_user, "Generate_File")
            if (content_values['success']):
                content.append(content_values['data'])   

    responseData['data']['content'] = content
    output_tokens = count_tokens(responseData['data']['textContent'])
    op_details = {'type': "LIST_MESSAGE", 'timestamp':  timestamp, 'outputTokens': output_tokens }
    record_thread_usage(op_details, info)

    return {
        'success': True,
        'message': 'Chat completed successfully',
        'data': responseData  
    }



def get_response_values(file, content_type, file_key, current_user, file_name = None):
    print("Get response Values")
    values = {}
    presigned_url = send_file_to_s3(file.content, file_key, file_name, current_user, content_type)
    file_size = get_file_size(file)
    if (presigned_url['success']):
        values['file_key'] = file_key
        values['presigned_url'] = presigned_url['presigned_url']
        values['file_size'] = file_size
    
    if ('png' in content_type) and (file_size > 204800): #Greater than 200KB 
        print("File was too large!")
        # Create a low-resplution version of the file
        file_key_low_res = file_key + '-low-res'
        low_res_file_content = create_low_res_version(file)
        presigned_url_low_res = send_file_to_s3(low_res_file_content, file_key_low_res, file_name, current_user, content_type)
        if (presigned_url_low_res['success']):
            values['file_key_low_res'] = file_key_low_res
            values['presigned_url_low_res'] = presigned_url['presigned_url']

    if (values):
        # print("Values for image key/presigned_url: ", values)
        return {'success': True, 'data' : {'type': content_type,'values': values}}
    return {'success': False, 'error': 'Failed to send file to s3 and get presigned url'}


def get_file_size(file):
    with BytesIO(file.content) as file_bytes:
        file_bytes.seek(0, 2)  # Move the pointer to the end of the file
        file_size = file_bytes.tell()  # Get the current file position (which is the file size)
    return file_size
    
def record_thread_usage(op_details, info):
    print("Recording thread usage")
    dynamodb = boto3.resource('dynamodb')
    usage_table = dynamodb.Table(os.environ['BILLING_DYNAMODB_TABLE'])

    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
   
    current_user = info['current_user']
    entry_key = f"{info['thread_key']}/{info['assistant_key']}"


    if (op_details['type'] == "CREATE_THREAD"):
        details = {
            'sessions': [{'start_time': op_details['timestamp'], 'operations': [op_details]}],
            'thread_id': info['thread_id'],
            'assistant_id': info['assistant_id'],
            'itemType': "codeInterpreter"
            }
        new_item = {
                    'id': entry_key, 
                    'accountId': info['account_id'],
                    'details': details,
                    'modelId': model,
                    'time': timestamp,
                    'user': current_user,
                    'requestId': info['request_id'],
                    }
        usage_table.put_item(Item=new_item)
        print("New Entry: ", usage_table.get_item(Key={'id': entry_key})['Item'])
        
        add_session_billing_table(timestamp, info)
        

    else:
        response = usage_table.get_item(Key={'id': entry_key})
        
        if 'Item' not in response:
            print("Failed to find record of thread usage!")
            return

        usage_item = response['Item']
        # Authorization check: the user making the request should own the assistant
        if usage_item['user'] != current_user:
            print('Not authorized to access this Thread Usage data')
            return

        details = get(usage_item, 'details')
        sessions = details.get('sessions', [])
        
        if (not sessions): # just incase sessions get deleted on the backend or something 
            details = {
            'sessions': [{'start_time': op_details['timestamp'], 'operations': [op_details]}],
            'thread_id': info['thread_id'],
            'assistant_id': info['assistant_id']
            }
            add_session_billing_table(timestamp, info)

        else:
        # Get the last session
            last_session = sessions[-1] 
            session_start_time = last_session.get('start_time')
            current_timestamp = op_details['timestamp']
            
            # Compare the time difference between current operation and the initial session start
            time_diff = current_timestamp - session_start_time

            if time_diff <= 3600000:  # Assuming time difference is in milliseconds and 3600000 ms = 1 hour
                # If within an hour, add the current operation to the last session
                last_session['operations'].append(op_details)
            else:
                # If not within an hour, create a new session
                sessions.append({'start_time': op_details['timestamp'], 'operations': [op_details]})
                add_session_billing_table(timestamp, info)

        # Update the DynamoDB item with the modified details
        usage_table.update_item(
            Key={'id': entry_key},
            UpdateExpression="set details = :d",
            ExpressionAttributeValues={':d': details}
        )
        print("Updated Entry: ", usage_table.get_item(Key={'id': entry_key})['Item'])
    print("Successfully recorded thread usage")


def add_session_billing_table(timestamp, info):
    print("Recording session to billing")
    dynamodb = boto3.resource('dynamodb')
    billing_table = dynamodb.Table(os.environ['BILLING_DYNAMODB_TABLE'])
    billing_table.put_item(Item={
                    'id': f'{str(uuid.uuid4())}',
                    'accountId': info['account_id'],
                    'itemType': 'codeInterpreterSession',
                    'modelId': model,
                    'requestId': info['request_id'],
                    'time': timestamp,
                    'user': info['current_user']
                })
    print("Billing session recorded")




def record_thread_run_data(run_data, run_status, info):
    print("Adding run to dynamo table")
    user_id = info['current_user']
    dynamodb = boto3.resource('dynamodb')
    runs_table = dynamodb.Table(os.environ['ASSISTANT_THREAD_RUNS_DYNAMODB_TABLE'])
    timestamp = int(time.time() * 1000)
    run_key = f'{user_id}/run/{str(uuid.uuid4())}'
   
    # DynamoDB new item to represent the run
    new_item = {
        'id': run_key,
        'data': run_data,
        'thread_key': info['thread_key'],
        'assistant_key': info['assistant_key'],
        'user': user_id,
        'createdAt': timestamp,
        'updatedAt': timestamp,
        'status': run_status,
        'assistant': 'codeInterpreter'
    }
    runs_table.put_item(Item=new_item)
    print("Successfully recorded run")


def get_thread(thread_key, user_id):
    dynamodb = boto3.resource('dynamodb')
    threads_table = dynamodb.Table(os.environ['ASSISTANT_THREADS_DYNAMODB_TABLE'])

    # Fetch the thread item from DynamoDB
    try:
        response = threads_table.get_item(Key={'id': thread_key})
        
        if 'Item' not in response:
            return {'success': False, 'error': 'Thread not found'}

        item = response['Item']
        # Check user authorization
        if item['user'] != user_id:
            return {'success': False, 'error': 'Not authorized to access this thread'}

        # Extract the OpenAI thread ID from the item
        openai_thread_id = get(item, 'data', openai_provider, 'threadId')

        if not openai_thread_id:
            return {'success': False, 'error': 'Thread not found'}

        # Return the thread info with thread_key and OpenAI thread ID
        return {
            'success': True,
            'thread_key': thread_key,
            'openai_thread_id': openai_thread_id
        }

    except ClientError as e:
        print(e.response['Error']['Message'])
        return {'success': False, 'error': str(e)}
       


def get_assistant(assistant_id, current_user): 
    dynamodb = boto3.resource('dynamodb')
    assistantstable = dynamodb.Table(os.environ['ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE'])

    try:
        # Fetch the assistant from DynamoDB
        print("Assistant key: ", assistant_id)
        response = assistantstable.get_item(Key={'id': assistant_id})
        
        if 'Item' not in response:
            return {'success': False, 'error': 'Assistant not found'}

        assistant_item = response['Item']
        # Authorization check: the user making the request should own the assistant
        if assistant_item['user'] != current_user:
            return {'success': False, 'error': 'Not authorized to access this assistant'}

    # Extract the OpenAI assistant ID from the item
        provider_assistant_id = get(assistant_item, 'data', 'assistantId')

        # If we have a valid OpenAI assistant ID, return the successful result
        if provider_assistant_id:
            return {
                'success': True,
                'assistant_key': assistant_id,
                'provider_assistant_id': provider_assistant_id
            }
        else:
            return {'success': False, 'error': 'Assistant not found'}

    except ClientError as e:
        # DynamoDB client error handling
        print(e.response['Error']['Message'])
        return {'success': False, 'error': str(e)}
    
# Check assistance exist, added for code reuse for chat_with_assistant and chat_with_code_interpreter
def check_assistant_exists(assistant_id, current_user):
    assistant_info = get_assistant(assistant_id, current_user)
    if not assistant_info['success']:
        return assistant_info  # Return error if any
    
    print(f"Assistant info: {assistant_info}")

    provider_assistant_id = assistant_info['provider_assistant_id']

    if not provider_assistant_id:
        return {'success': False, 'message': 'Assistant not found'}

    return {'success': True, 'provider_assistant_id': provider_assistant_id}




def delete_thread_by_id(thread_id, user_id):
    dynamodb = boto3.resource('dynamodb')
    threads_table = dynamodb.Table(os.environ['ASSISTANT_THREADS_DYNAMODB_TABLE'])

    # Fetch the thread from DynamoDB
    response = threads_table.get_item(Key={'id': thread_id})
    if 'Item' not in response:
        return {'success': False, 'message': 'Thread not found'}

    # Authorization check
    item = response['Item']
    if item['user'] != user_id:
        return {'success': False, 'message': 'You are not authorized to delete this thread'}

    openai_thread_id = get(item, 'data', 'openai', 'threadId')

    # Ensure thread_id is valid
    print(f"Deleting thread: {thread_id}=>openai=>{openai_thread_id}")
    if not openai_thread_id:
        return {'success': False, 'message': 'Thread not found'}

    # Delete the thread using the OpenAI Client
    result = client.beta.threads.delete(openai_thread_id)

    if result.deleted:
        # If the delete operation was successful, delete the entry from DynamoDB as well
        threads_table.delete_item(Key={'id': thread_id})
        return {'success': True, 'message': 'Thread deleted successfully'}
    else:
        return {'success': False, 'message': 'Thread could not be deleted'}



def create_new_openai_assistant(
        assistant_name,
        instructions,
        file_keys,
        tools
):
    print("Creating assistant with ", openai_provider)
    # Create a new assistant using the OpenAI Client

    # limited to only 20 files total per assistant in general by openai/azure
    recent_file_keys = file_keys[-20:] if len(file_keys) > 20 else file_keys
    print("File keys: ", recent_file_keys)

    file_ids = file_keys_to_file_ids(recent_file_keys)

    assistant = (
                client.beta.assistants.create(
                    name=assistant_name,
                    instructions=instructions,
                    tools=tools,
                    model=model,
                    tool_resources={
                        "code_interpreter": {
                        "file_ids": file_ids
                        }
                    }
                ) if openai_provider == "azure" else 
                client.beta.assistants.create(
                    instructions=instructions,
                    model=model,
                    tools=tools,
                    tool_resources={
                        "code_interpreter": {
                        "file_ids": file_ids
                        }
                    }
                )
                )
    
    if (assistant.id):
        # Return success response
        return {
            'success': True,
            'message': 'Assistant created successfully',
            'data': {'assistantId': assistant.id, 'provider': openai_provider}
        }
    return {'success': False, 'error': "Failed to create assistant with openai"}


def create_new_assistant(
        user_id,
        assistant_name,
        description,
        instructions,
        tags,
        file_keys,
        tools
):
    dynamodb = boto3.resource('dynamodb') 
    assistants_table = dynamodb.Table(os.environ['ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE'])
    timestamp = int(time.time() * 1000)

    for file_key in file_keys:
        file_key_user = file_key.split('//')[1]
        if ('@' not in file_key_user) or len(file_key_user) < 6 or (user_id not in file_key_user):
            return {'success': False, 'error': 'You are not authorized to access the referenced files'}

    assistant_info = create_new_openai_assistant(assistant_name, instructions, file_keys, tools)
    if (not assistant_info['success']):
        return assistant_info


    id_key = f'{user_id}/ast/{str(uuid.uuid4())}'

    # DynamoDB new item structure for the assistant
    new_item = {
        'id': id_key,
        'user': user_id,
        'assistant': assistant_name,
        'description': description,
        'instructions': instructions,
        'tags': tags,
        'createdAt': timestamp,
        'updatedAt': timestamp,
        'fileKeys': file_keys,
        'data': assistant_info['data'] 
    }

    # Put the new item into the DynamoDB table
    assistants_table.put_item(Item=new_item)
    print("Put item in ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE table")
    # Return success response
    return {
        'success': True,
        'message': 'Assistant created successfully',
        'data': {'assistantId': id_key}
    }


def delete_assistant_by_id(assistant_id, user_id):
    dynamodb = boto3.resource('dynamodb')
    assistants_table = dynamodb.Table(os.environ['ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE'])

    # Check if the assistant belongs to the user
    try:
        response = assistants_table.get_item(Key={'id': assistant_id})
    except ClientError as e:
        print(e.response['Error']['Message'])
        return {'success': False, 'message': 'Assistant not found'}

    if 'Item' not in response:
        return {'success': False, 'message': 'Assistant not found'}

    item = response['Item']

    # Auth check: verify ownership
    if item['user'] != user_id:
        return {'success': False, 'message': 'Not authorized to delete this assistant'}

    # Retrieve the OpenAI assistant ID
    openai_assistant_id = item['data']['openai']['assistantId']  # Or use your `get` utility function

    # Delete the assistant from OpenAI
    try:
        assistant_deletion_result = client.beta.assistants.delete(assistant_id=openai_assistant_id)
    except Exception as e:
        return {'success': False, 'message': f'Failed to delete OpenAI assistant: {e}'}

    if not assistant_deletion_result.deleted:
        return {'success': False, 'message': 'Failed to delete OpenAI assistant'}

    # Delete the assistant record in DynamoDB
    try:
        assistants_table.delete_item(Key={'id': assistant_id})
    except ClientError as e:
        print(e.response['Error']['Message'])
        return {'success': False, 'message': 'Failed to delete assistant record from database'}

    return {'success': True, 'message': 'Assistant deleted successfully'}



