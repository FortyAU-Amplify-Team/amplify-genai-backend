from decimal import Decimal
from importlib.metadata import metadata

from common.encoders import DecimalEncoder
from common.validate import validated
import json
import requests
import os
import boto3
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['OP_LOG_DYNAMO_TABLE'])

def log_execution(current_user, data, code, message, result, metadata={}):
    try:
        if not os.environ.get('OP_TRACING_ENABLED', 'false').lower() == 'true':
            return

        timestamp = datetime.utcnow().isoformat()

        # If there is metadata start_time, use it as the timestamp
        if 'start_time' in metadata:
            timestamp = metadata['start_time']
            # convert it to the right format
            timestamp = timestamp.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

        log_item = json.loads(json.dumps({
            'user': current_user,
            'timestamp': timestamp,
            'metadata': metadata,
            'conversationId': data['conversation'],
            'messageId': data['message'],
            'assistantId': data.get('assistant',"chat"),
            'actionName': data['action']['name'],
            'resultCode': code,
            'resultMessage': message,
            'operationDefinition': data['operationDefinition'],
            'actionPayload': data['action'].get('payload',{}) if os.environ.get('OP_TRACING_REQUEST_DETAILS_ENABLED', 'false').lower() == 'true' else None,
            'result': result if os.environ.get('OP_TRACING_RESULT_DETAILS_ENABLED', 'false').lower() == 'true' else None
        }, cls=DecimalEncoder), parse_float=Decimal)

        log_item = {k: v for k, v in log_item.items() if v is not None}

        # We have to make sure that we stay in the size limits of DynamoDB rows
        item_size = len(json.dumps(log_item, cls=DecimalEncoder))
        if item_size > 400000:
            for key in ['result', 'actionPayload', 'operationDefinition']:
                if key in log_item:
                    del log_item[key]
                    if len(json.dumps(log_item, cls=DecimalEncoder)) <= 400000:
                        break

        table.put_item(Item=log_item)
    except Exception as e:
        print(f"Error logging execution: {str(e)}")


def build_amplify_api_action(current_user, token, data):
    base_url = os.environ.get('API_BASE_URL', None)
    if not base_url:
        raise ValueError("API_BASE_URL environment variable is not set")

    endpoint = data["operationDefinition"]["url"]
    url = f"{base_url}{endpoint}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "data": data["action"]["payload"]
    }

    def send_request():
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()

        result = None
        if response.status_code == 200:
            result = response.json()

        return response.status_code, response.reason, result

    return send_request


def build_http_action(current_user, data):
    # Extract request details
    method = data["RequestType"]
    url = data["URL"]
    params = data.get("Parameters", {})
    body = data.get("Body", {})
    headers = data.get("Headers", {})
    auth = data.get("Auth", {})

    # Set up authentication if provided
    auth_instance = None
    if auth:
        if auth["type"].lower() == "bearer":
            headers["Authorization"] = f"Bearer {auth['token']}"
        elif auth["type"].lower() == "basic":
            auth_instance = requests.auth.HTTPBasicAuth(
                auth["username"], auth["password"]
            )

    def action():
        # Make the request
        response = requests.request(
            method=method,
            url=url,
            params=params,
            json=body if body else None,
            headers=headers,
            auth=auth_instance,
        )

        if response.status_code == 200:
            return response.status_code, response.reason, response.json()

        return response.status_code, response.reason, None

    return action

def build_action(current_user, token, data):
    #return build_http_action(current_user, data)
    action_type = data.get("operationDefinition", {}).get("type", "custom")

    if action_type != "http":
        print("Building Amplify API action.")
        return build_amplify_api_action(current_user, token, data)
    else:
        print("Building HTTP action.")
        return build_http_action(current_user, data)

    print("Unknown operation type.")
    return lambda : (200, "Unknown operation type.", {"data": "Please double check the operation defintion."})

@validated("execute_custom_auto")
def execute_custom_auto(event, context, current_user, name, data):
    try:
        # print("Nested data:", data["data"])
        token = data["access_token"]
        nested_data = data["data"]



        conversation_id = nested_data["conversation"]
        message_id = nested_data["message"]
        assistant_id = nested_data["assistant"]

        # Log the conversation and message IDs
        action_name = nested_data.get("action", {}).get("name", "unknown")
        print(f"Executing action: {action_name}")
        print(f"Payload keys: {nested_data.get('action', {}).get('payload', {}).keys()}")
        print(f"Conversation ID: {conversation_id}")
        print(f"Message ID: {message_id}")
        print(f"Assistant ID: {assistant_id}")

        action = build_action(current_user, token, nested_data)

        if action is None:
            print("The specified operation was not found.")
            return 404, "The specified operation was not found. Double check the name and ID of the action.", None

        try:
            #Log the execution time
            print("Executing action...")
            start_time = datetime.now()
            code, message, result = action()
            end_time = datetime.now()

            print(f"Execution time: {end_time - start_time}")

            # Create metadata that captures start_time and end_time in camel case and converts to isoformat
            metadata = {
                "startTime": start_time.isoformat(),
                "endTime": end_time.isoformat(),
                "executionTime": str(end_time - start_time)
            }

            log_execution(current_user, nested_data, code, message, result, metadata)

            # Return the response content
            return {
                'success': True,
                'data': {
                    'code': code,
                    'message': message,
                    'result': result
                }
            }
        except Exception as e:
            error_result = {
                'success': False,
                'data': {
                    'code': 500,
                    'message': f"An unexpected error occurred: {str(e)}",
                    'result': None
                }
            }
            log_execution(current_user, nested_data, 500, f"An unexpected error occurred: {str(e)}", error_result)

            print(f"An error occurred while executing the action: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    except Exception as e:
        error_result = {
            'success': False,
            'data': {
                'code': 500,
                'message': f"An unexpected error occurred: {str(e)}",
                'result': None
            }
        }
        log_execution(current_user, data.get('data',{}), 500,  f"An unexpected error occurred: {str(e)}", error_result)

        return f"An unexpected error occurred: {str(e)}"
