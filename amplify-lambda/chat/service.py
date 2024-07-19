from llm.chat import chat
import os
from common.validate import validated

@validated(op = 'chat')
def chat_endpoint(event, context, current_user, name, data):
    try:
        payload = data['data']
        print(payload)
        chat_url = os.environ['CHAT_ENDPOINT']
        access_token = data['access_token']

        response, metadata = chat(chat_url, access_token, payload)
        return {"success": True, "message": "Chat completed successfully", "data": response}
    except Exception as e:
        return {"success": False, "message": {f"Error: {e}"}}