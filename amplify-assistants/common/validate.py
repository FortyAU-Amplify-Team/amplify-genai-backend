from common.permissions import get_permission_checker
import json
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from common.encoders import CombinedEncoder

import os
import requests
from jose import jwt

from dotenv import load_dotenv

import boto3
import json
from datetime import datetime
from botocore.exceptions import ClientError
import re

load_dotenv(dotenv_path=".env.local")

ALGORITHMS = ["RS256"]


class HTTPException(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class BadRequest(HTTPException):
    def __init__(self, message="Bad Request"):
        super().__init__(400, message)


class Unauthorized(HTTPException):
    def __init__(self, message="Unauthorized"):
        super().__init__(401, message)


class NotFound(HTTPException):
    def __init__(self, message="Not Found"):
        super().__init__(404, message)


delete_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "description": "The public id of the assistant"
        },
    },
    "required": ["assistantId"]
}

create_assistant_schema = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "The name of the item"
        },
        "description": {
            "type": "string",
            "description": "A brief description of the item"
        },
        "assistantId": {
            "type": "string",
            "description": "The public id of the assistant"
        },
        "tags": {
            "type": "array",
            "description": "A list of tags associated with the item",
            "items": {
                "type": "string"
            }
        },
        "instructions": {
            "type": "string",
            "description": "Instructions related to the item"
        },
        "disclaimer": {
            "type": "string",
            "description": "Appended assistant response disclaimer related to the item"
        },
        "uri": {
            "oneOf": [
                {
                    "type": "string",
                    "description": "The endpoint that receives requests for the assistant"
                },
                {
                    "type": "null"
                }
            ]
        },
        "dataSources": {
            "type": "array",
            "description": "A list of data sources",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "The key of the data source"
                    }
                }
            }
        },
        "tools": {
            "type": "array",
            "description": "A list of tools associated with the item",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "The type of tool"
                    }
                }
            }
        }
    },
    "required": ["name", "description", "tags", "instructions", "dataSources", "tools"]
}


create_code_interpreter_assistant_schema = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "The name of the item"
        },
        "description": {
            "type": "string",
            "description": "A brief description of the item"
        },
        "tags": {
            "type": "array",
            "description": "A list of tags associated with the item",
            "items": {
                "type": "string"
            }
        },
        "instructions": {
            "type": "string",
            "description": "Instructions related to the item"
        },
        "fileKeys": {
            "type": "array",
            "description": "A list of data sources keys",
            "items": {
                "type": "string"
            }
        },
        "tools": {
            "type": "array",
            "description": "A list of tools associated with the item",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "The type of tool"
                    }
                }
            }
        }
    },
    "required": ["name", "description", "tags", "instructions", "fileKeys", "tools"]
}

share_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {"type": "string"},
        "recipientUsers": {"type": "array", "items": {"type": "string"}},
        "accessType": {"type": "string"},
        "dataSources": {"type": "array", "items": {"type": "string"}},
        "policy": {"type": "string", "default": ""}
    },
    "required": ["assistantId", "recipientUsers", "accessType"],
    "additionalProperties": False
}


chat_assistant_schema = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string"
        },
        "accountId": {
            "type": "string"
        },
        "requestId": {
            "type": "string"
        },
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string"
                    },
                    "content": {
                        "type": "string"
                    },
                    "role": {
                        "type": "string"
                    },
                    "type": {
                        "type": "string"
                    },
                    "data": {
                        "type": "object",
                        "additionalProperties": True
                    },
                    "codeInterpreterMessageData": {
                        "type": "object",
                        "properties": {
                            "threadId": {"type": "string"},
                            "role": {"type": "string"},
                            "textContent": {"type": "string"},
                            "content": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {
                                            "enum": ["image_file", "file", "application/pdf", "text/csv", "image/png"]
                                        },
                                        "values": {
                                            "type": "object",
                                            "properties": {
                                                "file_key": {"type": "string"},
                                                "presigned_url": {"type": "string"},
                                                "file_key_low_res": {"type": "string"},
                                                "presigned_url_low_res": {"type": "string"},
                                                "file_size": {"type": "integer"}
                                            },
                                            "required": ["file_key", "presigned_url"],
                                            "additionalProperties": False
                                        }
                                    },
                                    "required": ["type", "values"]
                                }
                            }
                        },
                        "required": []
                    }
                },
                "required": ["id", "content", "role"]
            }
        }
    },
    "required": ["id", "messages", "accountId", "requestId"]
}



key_request_schema = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string",
            "description": "Key."
        }
    },
    "required": ["key"]
}


"""
Every service must define the permissions for each operation here. 
The permission is related to a request path and to a specific operation.
"""
validators = {
    "/assistant/create": {
        "create": create_assistant_schema
    },
    "/assistant/delete": {
        "delete": delete_assistant_schema
    },
    "/assistant/share": {
        "share_assistant": share_assistant_schema
    },
    "/assistant/chat_with_code_interpreter": {
        "chat": chat_assistant_schema
    },
    "/": {
        "chat": chat_assistant_schema
    },
    "/assistant/create/codeinterpreter": {
        "create": create_code_interpreter_assistant_schema
    },
    "/assistant/files/download/codeinterpreter": {
        "download": key_request_schema
    },
}


def validate_data(name, op, data):
    if name in validators and op in validators[name]:
        schema = validators[name][op]
        try:
            validate(instance=data.get("data"), schema=schema)
        except ValidationError as e:
            print(e)
            raise ValidationError(f"Invalid data: {e.message}")
        print("Data validated")


def parse_and_validate(current_user, event, op, validate_body=True):
    data = {}
    if validate_body:
        try:
            data = json.loads(event['body']) if event.get('body') else {}
        except json.decoder.JSONDecodeError as e:
            raise BadRequest("Unable to parse JSON body.")

    name = event['path'] if event.get('path') else '/'

    if not name:
        raise BadRequest("Unable to perform the operation, invalid request.")

    try:
        if validate_body:
            validate_data(name, op, data)
    except ValidationError as e:
        raise BadRequest(e.message)

    permission_checker = get_permission_checker(current_user, name, op, data)

    if not permission_checker(current_user, data):
        # Return a 403 Forbidden if the user does not have permission to append data to this item
        raise Unauthorized("User does not have permission to perform the operation.")

    return [name, data]


def validated(op, validate_body=True):
    def decorator(f):
        def wrapper(event, context):
            try:

                token = parseToken(event)
                api_accessed = token[:4] == 'amp-'

                claims = api_claims(event, context, token) if (api_accessed) else get_claims(event, context, token)

                current_user = claims['username']
                print(f"User: {current_user}")
                if current_user is None:
                    raise Unauthorized("User not found.")

                [name, data] = parse_and_validate(current_user, event, op, validate_body)

                data['access_token'] = token
                data['account'] = claims['account']
                print("Default account: ", claims['account'])

                result = f(event, context, current_user, name, data)

                return {
                    "statusCode": 200,
                    "body": json.dumps(result, cls=CombinedEncoder)
                }
            except HTTPException as e:
                return {
                    "statusCode": e.status_code,
                    "body": json.dumps({
                        "error": f"Error: {e.status_code} - {e}"
                    })
                }

        return wrapper

    return decorator


def get_claims(event, context, token):
    # https://cognito-idp.<Region>.amazonaws.com/<userPoolId>/.well-known/jwks.json

    oauth_issuer_base_url = os.getenv('OAUTH_ISSUER_BASE_URL')
    oauth_audience = os.getenv('OAUTH_AUDIENCE')

    jwks_url = f'{oauth_issuer_base_url}/.well-known/jwks.json'
    jwks = requests.get(jwks_url).json()

    header = jwt.get_unverified_header(token)
    rsa_key = {}
    for key in jwks["keys"]:
        if key["kid"] == header["kid"]:
            rsa_key = {
                "kty": key["kty"],
                "kid": key["kid"],
                "use": key["use"],
                "n": key["n"],
                "e": key["e"]
            }

    if rsa_key:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=ALGORITHMS,
            audience=oauth_audience,
            issuer=oauth_issuer_base_url
        )

        get_email = lambda text: text.split('_', 1)[1] if '_' in text else None

        user = get_email(payload['username'])

        # grab deafault account from accounts table 
        dynamodb = boto3.resource('dynamodb')
        accounts_table_name = os.getenv('ACCOUNTS_DYNAMO_TABLE')
        if not accounts_table_name:
            raise ValueError("ACCOUNTS_DYNAMO_TABLE is not provided.")

        table = dynamodb.Table(accounts_table_name)
        account = None
        try:
            response = table.get_item(Key={'user': user})
            if 'Item' not in response:
                raise ValueError(f"No item found for user: {user}")

            accounts = response['Item'].get('accounts', [])
            for acct in accounts:
                if acct['isDefault']:
                    account = acct['id']
                    
            if (not account):
                raise ValueError("No default account found.")
        except Exception as e:
            print(f"Error retrieving default account: {e}")
            raise

        payload['account'] = account
        payload['username'] = user
        return payload
    else:
        print("No RSA Key Found, likely an invalid OAUTH_ISSUER_BASE_URL")

    raise Unauthorized("No Valid Access Token Found")



def parseToken(event):
    token = None
    normalized_headers = {k.lower(): v for k, v in event['headers'].items()}
    authorization_key = 'authorization'

    if authorization_key in normalized_headers:
        parts = normalized_headers[authorization_key].split()

        if len(parts) == 2:
            scheme, token = parts
            if scheme.lower() != 'bearer':
                token = None

    if not token:
        raise Unauthorized("No Access Token Found")
    
    return token


def api_claims(event, context, token):
    print("API route was taken")

    # Set up DynamoDB connection
    dynamodb = boto3.resource('dynamodb')
    api_keys_table_name = os.getenv('API_KEYS_DYNAMODB_TABLE')
    if not api_keys_table_name:
        raise ValueError("API_KEYS_DYNAMODB_TABLE is not provided.")

    table = dynamodb.Table(api_keys_table_name)

    try:
        # Retrieve item from DynamoDB
        response = table.query(
            IndexName='ApiKeyIndex',
            KeyConditionExpression='apiKey = :apiKeyVal',
            ExpressionAttributeValues={
                ':apiKeyVal': token
            }
        )
        items = response['Items']


        if not items:
            print("API key does not exist.")
            raise LookupError("API key not found.")
        
        item = items[0]

        # Check if the API key is active
        if (not item.get('active', False)):
            print("API key is inactive.")
            raise PermissionError("API key is inactive.")

        # Optionally check the expiration date if applicable
        if (item.get('expirationDate') and datetime.strptime(item['expirationDate'], "%Y-%m-%d") <= datetime.now()):
            print("API key has expired.")
            raise PermissionError("API key has expired.")

        # Check for access rights
        access = item.get('accessTypes', [])
        if ('assistants' not in access and 'Full Access' not in access):
            print("API doesn't have access to assistants")
            raise PermissionError("API key does not have access to assistants functionality")

        # Update last accessed
        table.update_item(
            Key={'api_owner_id': item['api_owner_id']},
            UpdateExpression="SET lastAccessed = :now",
            ExpressionAttributeValues={':now': datetime.now().isoformat()}
        )
        print("Last Access updated")

        # Determine API user
        current_user = determine_api_user(item)

        return {'username': current_user, 'account': item['account']}

    except Exception as e:
        print("Error during DynamoDB operation:", str(e))
        raise RuntimeError("Internal server error occurred: ", e)


def determine_api_user(data):
    key_type_pattern = r"/(.*?)Key/"
    match = re.search(key_type_pattern, data['api_owner_id'])
    key_type = match.group(1) if match else None

    if key_type == 'owner':
        return data.get('owner')
    elif key_type == 'delegate':
        return data.get('delegate')
    elif key_type == 'system':
        return data.get('systemId')
    else:
        print("Unknown or missing key type in api_owner_id:", key_type)
        raise Exception("Invalid or unrecognized key type.")