import boto3
from botocore.exceptions import ClientError
import os


def get_user_groups(cognito, username, user_pool_id):
    try:
        groups = []
        pagination_token = None
        page_count = 0
        max_pages = 100

        while True:
            if page_count >= max_pages:
                print(f"Reached maximum number of pages ({max_pages})")
                break

            kwargs = {"Username": username, "UserPoolId": user_pool_id}
            if pagination_token:
                kwargs["NextToken"] = pagination_token

            response = cognito.admin_list_groups_for_user(**kwargs)
            page_count += 1

            current_groups = [
                group["GroupName"] for group in response.get("Groups", [])
            ]
            groups.extend(current_groups)

            pagination_token = response.get("NextToken")
            if not pagination_token:
                break

        return groups

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "UnknownError")
        print(f"Error getting user {username} groups. Error code: {error_code}")
        raise e


def remove_user_from_group(cognito, username, group_name, user_pool_id):
    try:
        cognito.admin_remove_user_from_group(
            UserPoolId=user_pool_id, Username=username, GroupName=group_name
        )
        print(f"Successfully removed user {username} from group {group_name}")
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "UnknownError")
        print(
            f"Error removing user {username} from group {group_name}. Error code: {error_code}"  # noqa: E501
        )
        raise e


def add_user_to_group(cognito, username, group_name, user_pool_id):
    try:
        response = cognito.admin_add_user_to_group(
            UserPoolId=user_pool_id, Username=username, GroupName=group_name
        )
        print(f"Successfully added user {username} to group {group_name}")
        return response
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "UnknownError")
        print(
            f"Error adding user {username} to group {group_name}. Error code: {error_code}"  # noqa: E501
        )
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            # ignore roles that do not exist.
            raise e


def override_token_groups(event, group_name):
    """Override the token's cognito:groups claim so the first federated token is correct."""  # noqa: E501
    response = event.setdefault("response", {})
    claims_override = response.get("claimsOverrideDetails") or {}
    claims_override["groupOverrideDetails"] = {"groupsToOverride": [group_name]}
    response["claimsOverrideDetails"] = claims_override
    print(f"Overriding token groups with: [{group_name}]")
    return event


def handler(event, context):
    print(f"Event received: {event}")

    # Determine the trigger type from triggerSource (event shapes overlap).
    trigger_source = event.get("triggerSource", "")

    if trigger_source.startswith("TokenGeneration_"):
        trigger_type = "PRE_TOKEN_GENERATION"
    elif trigger_source.startswith("PostConfirmation_"):
        trigger_type = "POST_CONFIRMATION"
    elif trigger_source.startswith("PreSignUp_"):
        trigger_type = "PRE_SIGN_UP"
    elif trigger_source.startswith("PostAuthentication_"):
        trigger_type = "POST_AUTHENTICATION"
    elif trigger_source.startswith("PreAuthentication_"):
        trigger_type = "PRE_AUTHENTICATION"
    else:
        print(f"Unhandled trigger source: {trigger_source}")
        return event

    user_attributes = event.get("request", {}).get("userAttributes", {})
    if not user_attributes:
        print("No user attributes found in event")
        return event

    # Cognito provides the username at the top level of the event.
    username = (
        event.get("userName")
        or user_attributes.get("sub")
        or user_attributes.get("email")
    )
    new_group = user_attributes.get("custom:chatbot_role")
    user_pool_id = event["userPoolId"]

    print(f"Trigger type: {trigger_type}")
    print(f"User attributes: {user_attributes}")
    print(f"Username: {username}")
    print(f"New group: {new_group}")
    print(f"User pool ID: {user_pool_id}")

    # Get default group from environment variable or use 'user' as fallback
    default_group = os.environ.get("DEFAULT_USER_GROUP", "user")

    # If no custom:chatbot_role is provided, use default group
    if not new_group:
        new_group = default_group
        print(f"No custom:chatbot_role found, using default group: {default_group}")

    # PRE_SIGN_UP fires before the federated user exists, so we cannot assign
    # groups here. The actual assignment happens on PRE_TOKEN_GENERATION, which
    # runs after the user is created and on every subsequent sign-in.
    if trigger_type == "PRE_SIGN_UP":
        print("Pre sign-up trigger - user does not exist yet; deferring assignment")
        print(f"Resolved group for later assignment: {new_group}")

        # Persist the resolved group on the attribute so it is available later.
        if "custom:chatbot_role" not in user_attributes:
            user_attributes["custom:chatbot_role"] = new_group
            print(f"Added custom:chatbot_role attribute: {new_group}")

        return event

    # For triggers where the user already exists
    # (POST_CONFIRMATION, POST_AUTHENTICATION, PRE_TOKEN_GENERATION).
    if username:
        cognito = boto3.client("cognito-idp")

        current_groups = get_user_groups(
            cognito=cognito, username=username, user_pool_id=user_pool_id
        )

        print(f"Current groups for user {username}: {current_groups}")

        # Remove user from all groups except the new one
        for group in current_groups:
            if group != new_group:
                remove_user_from_group(cognito, username, group, user_pool_id)

        # Add user to the new group if not already in it
        if new_group not in current_groups:
            add_user_to_group(
                cognito=cognito,
                username=username,
                group_name=new_group,
                user_pool_id=user_pool_id,
            )
        else:
            print(f"User {username} is already in group {new_group}")

        # For Pre Token Generation, override the groups claim so the first token is correct.  # noqa: E501
        if trigger_type == "PRE_TOKEN_GENERATION":
            event = override_token_groups(event, new_group)
    else:
        print("No username found in user attributes, skipping group assignment")

    return event
