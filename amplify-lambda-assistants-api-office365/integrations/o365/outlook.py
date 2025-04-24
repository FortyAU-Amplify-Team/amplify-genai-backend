import json
import requests
from typing import Dict, List, Optional, Union
from datetime import datetime
from integrations.oauth import get_ms_graph_session

integration_name = "microsoft_outlook"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"

class OutlookError(Exception):
    """Base exception for Outlook operations"""
    pass

class MessageNotFoundError(OutlookError):
    """Raised when a message cannot be found"""
    pass

class FolderNotFoundError(OutlookError):
    """Raised when a mail folder cannot be found"""
    pass

class AttachmentError(OutlookError):
    """Raised when attachment operations fail"""
    pass

def handle_graph_error(response: requests.Response) -> None:
    """Common error handling for Graph API responses"""
    if response.status_code == 404:
        error_message = response.json().get('error', {}).get('message', '').lower()
        if 'message' in error_message:
            raise MessageNotFoundError("Message not found")
        elif 'folder' in error_message:
            raise FolderNotFoundError("Folder not found")
        raise OutlookError("Resource not found")
        
    try:
        error_data = response.json()
        error_message = error_data.get('error', {}).get('message', 'Unknown error')
    except json.JSONDecodeError:
        error_message = response.text
    raise OutlookError(f"Graph API error: {error_message} (Status: {response.status_code})")

def list_messages(current_user: str, folder_id: str = "Inbox", 
                 top: int = 10, skip: int = 0, 
                 filter_query: Optional[str] = None, access_token: str = None) -> List[Dict]:
    """
    Lists messages in a specified mail folder with pagination and filtering support.
    
    Args:
        current_user: User identifier
        folder_id: Folder ID or well-known name (default: "Inbox")
        top: Maximum number of messages to retrieve
        skip: Number of messages to skip
        filter_query: OData filter query
    
    Returns:
        List of message details
    
    Raises:
        FolderNotFoundError: If folder doesn't exist
        OutlookError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/mailFolders/{folder_id}/messages"
        
        params = {
            "$top": top,
            "$skip": skip,
            "$orderby": "receivedDateTime desc"
        }
        
        if filter_query:
            params["$filter"] = filter_query
            
        response = session.get(url, params=params)
        
        if not response.ok:
            handle_graph_error(response)
            
        messages = response.json().get('value', [])
        return [format_message(msg) for msg in messages]
        
    except requests.RequestException as e:
        raise OutlookError(f"Network error while listing messages: {str(e)}")

def get_message_details(current_user: str, message_id: str, 
                       include_body: bool = True, access_token: str = None) -> Dict:
    """
    Gets detailed information about a specific message.
    
    Args:
        current_user: User identifier
        message_id: Message ID
        include_body: Whether to include message body
    
    Returns:
        Dict containing message details
    
    Raises:
        MessageNotFoundError: If message doesn't exist
        OutlookError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}"
        
        params = {}
        if include_body:
            params["$select"] = "id,subject,body,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,hasAttachments"
            
        response = session.get(url, params=params)
        
        if not response.ok:
            handle_graph_error(response)
            
        return format_message(response.json(), detailed=True)
        
    except requests.RequestException as e:
        raise OutlookError(f"Network error while fetching message: {str(e)}")

def send_mail(current_user: str, subject: str, body: str, 
              to_recipients: List[str], cc_recipients: Optional[List[str]] = None,
              bcc_recipients: Optional[List[str]] = None,
              importance: str = "normal", access_token: str = None) -> Dict:
    """
    Sends an email with support for CC, BCC, and importance levels.
    
    Args:
        current_user: User identifier
        subject: Email subject
        body: Email body content
        to_recipients: List of primary recipient email addresses
        cc_recipients: Optional list of CC recipient email addresses
        bcc_recipients: Optional list of BCC recipient email addresses
        importance: Message importance ('low', 'normal', 'high')
    
    Returns:
        Dict containing send status
    
    Raises:
        OutlookError: If send operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/sendMail"
        
        # Validate inputs
        if not subject or not body:
            raise OutlookError("Subject and body are required")
            
        if not to_recipients:
            raise OutlookError("At least one recipient is required")
            
        if importance not in ['low', 'normal', 'high']:
            raise OutlookError("Invalid importance level")
            
        # Validate email formats
        for email in to_recipients + (cc_recipients or []) + (bcc_recipients or []):
            if not '@' in email:
                raise OutlookError(f"Invalid email address format: {email}")

        email_msg = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body
                },
                "toRecipients": [{"emailAddress": {"address": addr}} for addr in to_recipients],
                "importance": importance
            },
            "saveToSentItems": "true"
        }
        
        if cc_recipients:
            email_msg["message"]["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc_recipients
            ]
            
        if bcc_recipients:
            email_msg["message"]["bccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in bcc_recipients
            ]

        response = session.post(url, json=email_msg)
        
        if not response.ok:
            handle_graph_error(response)
            
        return {
            "status": "sent",
            "subject": subject,
            "recipients": {
                "to": to_recipients,
                "cc": cc_recipients or [],
                "bcc": bcc_recipients or []
            }
        }
        
    except requests.RequestException as e:
        raise OutlookError(f"Network error while sending mail: {str(e)}")

def delete_message(current_user: str, message_id: str, access_token: str) -> Dict:
    """
    Deletes a message.
    
    Args:
        current_user: User identifier
        message_id: Message ID to delete
    
    Returns:
        Dict containing deletion status
    
    Raises:
        MessageNotFoundError: If message doesn't exist
        OutlookError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}"
        response = session.delete(url)
        
        if response.status_code == 204:
            return {"status": "deleted", "id": message_id}
            
        handle_graph_error(response)
        
    except requests.RequestException as e:
        raise OutlookError(f"Network error while deleting message: {str(e)}")

def get_attachments(current_user: str, message_id: str, access_token: str) -> List[Dict]:
    """
    Gets attachments for a specific message.
    
    Args:
        current_user: User identifier
        message_id: Message ID
    
    Returns:
        List of attachment details
    
    Raises:
        MessageNotFoundError: If message doesn't exist
        OutlookError: For other failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/attachments"
        response = session.get(url)
        
        if not response.ok:
            handle_graph_error(response)
            
        attachments = response.json().get('value', [])
        return [format_attachment(attachment) for attachment in attachments]
        
    except requests.RequestException as e:
        raise OutlookError(f"Network error while getting attachments: {str(e)}")

def format_message(message: Dict, detailed: bool = False) -> Dict:
    """Format message data consistently"""
    formatted = {
        'id': message['id'],
        'subject': message.get('subject', ''),
        'from': message.get('from', {}).get('emailAddress', {}).get('address', ''),
        'receivedDateTime': message.get('receivedDateTime', ''),
        'hasAttachments': message.get('hasAttachments', False),
        'importance': message.get('importance', 'normal'),
        'isDraft': message.get('isDraft', False),
        'isRead': message.get('isRead', False),
    }
    
    if detailed:
        formatted.update({
            'body': message.get('body', {}).get('content', ''),
            'bodyType': message.get('body', {}).get('contentType', 'text'),
            'toRecipients': [
                r.get('emailAddress', {}).get('address', '')
                for r in message.get('toRecipients', [])
            ],
            'ccRecipients': [
                r.get('emailAddress', {}).get('address', '')
                for r in message.get('ccRecipients', [])
            ],
            'bccRecipients': [
                r.get('emailAddress', {}).get('address', '')
                for r in message.get('bccRecipients', [])
            ],
            'categories': message.get('categories', []),
            'webLink': message.get('webLink', '')
        })
        
    return formatted

def format_attachment(attachment: Dict) -> Dict:
    """Format attachment data consistently"""
    return {
        'id': attachment['id'],
        'name': attachment.get('name', ''),
        'contentType': attachment.get('contentType', ''),
        'size': attachment.get('size', 0),
        'isInline': attachment.get('isInline', False),
        'lastModifiedDateTime': attachment.get('lastModifiedDateTime', '')
    }


def update_message(current_user: str, message_id: str, changes: Dict, access_token: str = None) -> Dict:
    """
    Updates properties of a specific message.
    
    Args:
        current_user: User identifier
        message_id: The ID of the message to update
        changes: Dictionary of properties to update (e.g., {"isRead": True})
        access_token: Optional access token
    
    Returns:
        Dict containing update status
    
    Raises:
        OutlookError: For update failures
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}"
        response = session.patch(url, json=changes)
        if not response.ok:
            handle_graph_error(response)
        return {"status": "updated", "id": message_id, "changes": changes}
    except requests.RequestException as e:
        raise OutlookError(f"Network error while updating message: {str(e)}")


def create_draft(current_user: str, subject: str, body: str, 
                 to_recipients: Optional[List[str]] = None, 
                 cc_recipients: Optional[List[str]] = None, 
                 bcc_recipients: Optional[List[str]] = None, 
                 importance: str = "normal", access_token: str = None) -> Dict:
    """
    Creates a draft message.
    
    Args:
        current_user: User identifier
        subject: Draft email subject
        body: Draft email body content
        to_recipients: Optional list of primary recipient email addresses
        cc_recipients: Optional list of CC recipient email addresses
        bcc_recipients: Optional list of BCC recipient email addresses
        importance: Importance level ('low', 'normal', 'high')
        access_token: Optional access token
    
    Returns:
        Dict containing the draft message details
    
    Raises:
        OutlookError: If creation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages"
        payload = {
            "subject": subject,
            "body": {
                "contentType": "text",
                "content": body
            },
            "importance": importance
        }
        if to_recipients:
            payload["toRecipients"] = [{"emailAddress": {"address": addr}} for addr in to_recipients]
        if cc_recipients:
            payload["ccRecipients"] = [{"emailAddress": {"address": addr}} for addr in cc_recipients]
        if bcc_recipients:
            payload["bccRecipients"] = [{"emailAddress": {"address": addr}} for addr in bcc_recipients]
            
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)

        response_data = response.json()
        return {
            "message_id": response_data.get("id"),
        }
    except requests.RequestException as e:
        raise OutlookError(f"Network error while creating draft: {str(e)}")


def send_draft(current_user: str, message_id: str, access_token: str = None) -> Dict:
    """
    Sends a draft message.
    
    Args:
        current_user: User identifier
        message_id: The ID of the draft message to send
        access_token: Optional access token
    
    Returns:
        Dict confirming the send action
    
    Raises:
        OutlookError: If sending fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/send"
        response = session.post(url, json={})
        if response.status_code not in [202, 204]:
            handle_graph_error(response)
        return {"status": "sent", "id": message_id}
    except requests.RequestException as e:
        raise OutlookError(f"Network error while sending draft: {str(e)}")


def reply_to_message(current_user: str, message_id: str, comment: str, access_token: str = None) -> Dict:
    """
    Sends a reply to a specific message.
    
    Args:
        current_user: User identifier
        message_id: The ID of the message to reply to
        comment: The reply comment content
        access_token: Optional access token
    
    Returns:
        Dict confirming the reply action
    
    Raises:
        OutlookError: If reply fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/reply"
        payload = {"comment": comment}
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return {"status": "replied", "id": message_id}
    except requests.RequestException as e:
        raise OutlookError(f"Network error while replying: {str(e)}")


def reply_all_message(current_user: str, message_id: str, comment: str, access_token: str = None) -> Dict:
    """
    Sends a reply-all to a specific message.
    
    Args:
        current_user: User identifier
        message_id: The ID of the message to reply to
        comment: The reply comment content
        access_token: Optional access token
    
    Returns:
        Dict confirming the reply-all action
    
    Raises:
        OutlookError: If reply-all fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/replyAll"
        payload = {"comment": comment}
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return {"status": "replied_all", "id": message_id}
    except requests.RequestException as e:
        raise OutlookError(f"Network error while replying all: {str(e)}")


def forward_message(current_user: str, message_id: str, comment: str, 
                    to_recipients: List[str], access_token: str = None) -> Dict:
    """
    Forwards a specific message.
    
    Args:
        current_user: User identifier
        message_id: The ID of the message to forward
        comment: The comment to include with the forwarded message
        to_recipients: List of recipient email addresses to forward the message to
        access_token: Optional access token
    
    Returns:
        Dict confirming the forward action
    
    Raises:
        OutlookError: If forward fails or recipients are missing
    """
    if not to_recipients:
        raise OutlookError("At least one recipient is required to forward a message")
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/forward"
        payload = {
            "comment": comment,
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in to_recipients]
        }
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return {"status": "forwarded", "id": message_id, "recipients": to_recipients}
    except requests.RequestException as e:
        raise OutlookError(f"Network error while forwarding message: {str(e)}")


def move_message(current_user: str, message_id: str, destination_folder_id: str, access_token: str = None) -> Dict:
    """
    Moves a specific message to a different folder.
    
    Args:
        current_user: User identifier
        message_id: The ID of the message to move
        destination_folder_id: The target folder ID
        access_token: Optional access token
    
    Returns:
        Dict containing the moved message details
    
    Raises:
        OutlookError: If the move operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/move"
        payload = {"destinationId": destination_folder_id}
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return format_message(response.json(), detailed=True)
    except requests.RequestException as e:
        raise OutlookError(f"Network error while moving message: {str(e)}")


def list_folders(current_user: str, access_token: str = None) -> List[Dict]:
    """
    Lists all mail folders.
    
    Args:
        current_user: User identifier
        access_token: Optional access token
    
    Returns:
        List of mail folder details
    
    Raises:
        OutlookError: If retrieval fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/mailFolders"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
        return response.json().get('value', [])
    except requests.RequestException as e:
        raise OutlookError(f"Network error while listing folders: {str(e)}")


def get_folder_details(current_user: str, folder_id: str, access_token: str = None) -> Dict:
    """
    Retrieves details of a specific mail folder.
    
    Args:
        current_user: User identifier
        folder_id: The ID of the mail folder
        access_token: Optional access token
    
    Returns:
        Dict containing folder details
    
    Raises:
        OutlookError: If retrieval fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/mailFolders/{folder_id}"
        response = session.get(url)
        if not response.ok:
            handle_graph_error(response)
        return response.json()
    except requests.RequestException as e:
        raise OutlookError(f"Network error while retrieving folder details: {str(e)}")


def add_attachment(current_user: str, message_id: str, name: str, content_type: str, 
                   content_bytes: str, is_inline: bool = False, access_token: str = None) -> Dict:
    """
    Adds an attachment to a specific message.
    
    Args:
        current_user: User identifier
        message_id: The ID of the message
        name: Attachment file name
        content_type: MIME type of the attachment
        content_bytes: Base64 encoded content of the attachment
        is_inline: Whether the attachment is inline (default: False)
        access_token: Optional access token
    
    Returns:
        Dict containing the added attachment details
    
    Raises:
        OutlookError: If the attachment operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/attachments"
        payload = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": content_type,
            "contentBytes": content_bytes,
            "isInline": is_inline
        }
        response = session.post(url, json=payload)
        if not response.ok:
            handle_graph_error(response)
        return format_attachment(response.json())
    except requests.RequestException as e:
        raise OutlookError(f"Network error while adding attachment: {str(e)}")


def delete_attachment(current_user: str, message_id: str, attachment_id: str, access_token: str = None) -> Dict:
    """
    Deletes a specific attachment from a message.
    
    Args:
        current_user: User identifier
        message_id: The ID of the message
        attachment_id: The ID of the attachment to delete
        access_token: Optional access token
    
    Returns:
        Dict confirming deletion
    
    Raises:
        OutlookError: If deletion fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages/{message_id}/attachments/{attachment_id}"
        response = session.delete(url)
        if response.status_code == 204:
            return {"status": "attachment deleted", "attachment_id": attachment_id}
        handle_graph_error(response)
    except requests.RequestException as e:
        raise OutlookError(f"Network error while deleting attachment: {str(e)}")

def search_messages(current_user: str, search_query: str, top: int = 10, skip: int = 0, access_token: str = None) -> List[Dict]:
    """
    Searches messages for a given query string using the Microsoft Graph API's $search parameter.
    
    Args:
        current_user: User identifier
        search_query: A string search query (e.g., "meeting")
        top: Maximum number of messages to return
        skip: Number of messages to skip for pagination
        access_token: Optional access token
    
    Returns:
        List of message details matching the search query
    
    Raises:
        OutlookError: If the search operation fails
    """
    try:
        session = get_ms_graph_session(current_user, integration_name, access_token)
        url = f"{GRAPH_ENDPOINT}/me/messages"
        params = {
            "$top": top,
            "$skip": skip,
            "$search": f'"{search_query}"'
        }
        # The Graph API requires the ConsistencyLevel header set to eventual when using $search
        session.headers.update({"ConsistencyLevel": "eventual"})
        response = session.get(url, params=params)
        if not response.ok:
            handle_graph_error(response)
        messages = response.json().get('value', [])
        return [format_message(msg) for msg in messages]
    except requests.RequestException as e:
        raise OutlookError(f"Network error while searching messages: {str(e)}")
