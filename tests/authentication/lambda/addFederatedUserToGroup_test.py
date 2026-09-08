import os
import sys
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

lambda_path = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "../../../lib/authentication/lambda/addFederatedUserToUserGroup",
    )
)
sys.path.insert(0, lambda_path)

from index import (  # noqa: E402
    handler,
    get_user_groups,
    add_user_to_group,
    remove_user_from_group,
)


@pytest.fixture
def cognito_event():
    return {
        "triggerSource": "TokenGeneration_HostedAuth",
        "userName": "EntraID_test-user-123",
        "request": {
            "userAttributes": {
                "sub": "test-user-123",
                "custom:chatbot_role": "TestGroup",
            }
        },
        "userPoolId": "us-east-1_testpool",
    }


@pytest.fixture
def mock_cognito():
    with patch("boto3.client") as mock_client:
        yield mock_client.return_value


def test_get_user_groups_success(mock_cognito):
    mock_cognito.admin_list_groups_for_user.return_value = {
        "Groups": [{"GroupName": "Group1"}, {"GroupName": "Group2"}]
    }

    groups = get_user_groups(mock_cognito, "test-user", "test-pool-id")

    assert groups == ["Group1", "Group2"]
    mock_cognito.admin_list_groups_for_user.assert_called_once_with(
        Username="test-user", UserPoolId="test-pool-id"
    )


def test_get_user_groups_with_pagination(mock_cognito):
    mock_cognito.admin_list_groups_for_user.side_effect = [
        {"Groups": [{"GroupName": "Group1"}], "NextToken": "token123"},
        {"Groups": [{"GroupName": "Group2"}]},
    ]

    groups = get_user_groups(mock_cognito, "test-user", "test-pool-id")

    assert groups == ["Group1", "Group2"]
    assert mock_cognito.admin_list_groups_for_user.call_count == 2


def test_get_user_groups_error(mock_cognito):
    mock_cognito.admin_list_groups_for_user.side_effect = ClientError(
        error_response={"Error": {"Code": "UserNotFoundException"}},
        operation_name="AdminListGroupsForUser",
    )

    with pytest.raises(ClientError):
        get_user_groups(mock_cognito, "test-user", "test-pool-id")


def test_add_user_to_group_success(mock_cognito):
    add_user_to_group(mock_cognito, "test-user", "test-group", "test-pool-id")

    mock_cognito.admin_add_user_to_group.assert_called_once_with(
        UserPoolId="test-pool-id", Username="test-user", GroupName="test-group"
    )


def test_remove_user_from_group_success(mock_cognito):
    remove_user_from_group(mock_cognito, "test-user", "test-group", "test-pool-id")

    mock_cognito.admin_remove_user_from_group.assert_called_once_with(
        UserPoolId="test-pool-id", Username="test-user", GroupName="test-group"
    )


def test_handler_new_group_assignment(mock_cognito):
    event = {
        "triggerSource": "PostAuthentication_Authentication",
        "userName": "test-user-123",
        "request": {
            "userAttributes": {
                "sub": "test-user-123",
                "custom:chatbot_role": "NewGroup",
            }
        },
        "userPoolId": "us-east-1_testpool",
    }

    mock_cognito.admin_list_groups_for_user.return_value = {
        "Groups": [{"GroupName": "OldGroup"}]
    }

    result = handler(event, None)

    assert result == event
    mock_cognito.admin_remove_user_from_group.assert_called_once()
    mock_cognito.admin_add_user_to_group.assert_called_once()


def test_handler_no_group_change(mock_cognito):
    event = {
        "triggerSource": "PostAuthentication_Authentication",
        "userName": "test-user-123",
        "request": {
            "userAttributes": {
                "sub": "test-user-123",
                "custom:chatbot_role": "ExistingGroup",
            }
        },
        "userPoolId": "us-east-1_testpool",
    }

    mock_cognito.admin_list_groups_for_user.return_value = {
        "Groups": [{"GroupName": "ExistingGroup"}]
    }

    result = handler(event, None)

    assert result == event
    mock_cognito.admin_remove_user_from_group.assert_not_called()
    mock_cognito.admin_add_user_to_group.assert_not_called()


def test_handler_no_chatbot_role(mock_cognito):
    event = {
        "triggerSource": "PostAuthentication_Authentication",
        "userName": "test-user-123",
        "request": {"userAttributes": {"sub": "test-user-123"}},
        "userPoolId": "us-east-1_testpool",
    }

    # Mock the response to prevent infinite pagination
    mock_cognito.admin_list_groups_for_user.return_value = {
        "Groups": []  # User has no current groups
    }

    result = handler(event, None)

    assert result == event
    # Should call admin_list_groups_for_user once to check current groups
    mock_cognito.admin_list_groups_for_user.assert_called_once()
    # Should add user to default group since they have no custom:chatbot_role
    mock_cognito.admin_add_user_to_group.assert_called_once_with(
        UserPoolId="us-east-1_testpool",
        Username="test-user-123",
        GroupName="user",  # default group
    )


def test_handler_pre_sign_up_does_not_assign(mock_cognito):
    # PRE_SIGN_UP fires for federated users but cannot assign groups yet,
    # so it must return early without calling the admin APIs.
    event = {
        "triggerSource": "PreSignUp_ExternalProvider",
        "userName": "EntraID_test-user-123",
        "request": {
            "userAttributes": {
                "email": "user@example.com",
                "custom:chatbot_role": "workspace_manager",
            }
        },
        "userPoolId": "us-east-1_testpool",
    }

    result = handler(event, None)

    assert result == event
    mock_cognito.admin_add_user_to_group.assert_not_called()
    mock_cognito.admin_remove_user_from_group.assert_not_called()


def test_handler_unknown_trigger_source_returns_early(mock_cognito):
    event = {
        "request": {
            "userAttributes": {
                "sub": "test-user-123",
                "custom:chatbot_role": "workspace_manager",
            }
        },
        "userPoolId": "us-east-1_testpool",
    }

    result = handler(event, None)

    assert result == event
    mock_cognito.admin_list_groups_for_user.assert_not_called()
    mock_cognito.admin_add_user_to_group.assert_not_called()


def test_handler_uses_username_over_sub(mock_cognito):
    # Cognito delivers the username at the top level as "userName"; it must
    # take precedence over sub when calling the admin APIs.
    event = {
        "triggerSource": "PostAuthentication_Authentication",
        "userName": "EntraID_top-level-name",
        "request": {
            "userAttributes": {
                "sub": "test-user-123",
                "custom:chatbot_role": "workspace_manager",
            }
        },
        "userPoolId": "us-east-1_testpool",
    }

    mock_cognito.admin_list_groups_for_user.return_value = {"Groups": []}

    handler(event, None)

    mock_cognito.admin_add_user_to_group.assert_called_once_with(
        UserPoolId="us-east-1_testpool",
        Username="EntraID_top-level-name",
        GroupName="workspace_manager",
    )


def test_handler_pre_token_generation_overrides_claim(mock_cognito):
    # PRE_TOKEN_GENERATION persists membership AND overrides the token's
    # groups claim so the first federated token is correct.
    event = {
        "triggerSource": "TokenGeneration_HostedAuth",
        "userName": "EntraID_test-user-123",
        "request": {
            "userAttributes": {
                "sub": "test-user-123",
                "custom:chatbot_role": "workspace_manager",
            }
        },
        "userPoolId": "us-east-1_testpool",
    }

    mock_cognito.admin_list_groups_for_user.return_value = {"Groups": []}

    result = handler(event, None)

    mock_cognito.admin_add_user_to_group.assert_called_once_with(
        UserPoolId="us-east-1_testpool",
        Username="EntraID_test-user-123",
        GroupName="workspace_manager",
    )
    assert result["response"]["claimsOverrideDetails"]["groupOverrideDetails"][
        "groupsToOverride"
    ] == ["workspace_manager"]


def test_handler_non_token_trigger_does_not_override_claim(mock_cognito):
    # Only PRE_TOKEN_GENERATION should set claimsOverrideDetails.
    event = {
        "triggerSource": "PostAuthentication_Authentication",
        "userName": "test-user-123",
        "request": {
            "userAttributes": {
                "sub": "test-user-123",
                "custom:chatbot_role": "workspace_manager",
            }
        },
        "userPoolId": "us-east-1_testpool",
    }

    mock_cognito.admin_list_groups_for_user.return_value = {"Groups": []}

    result = handler(event, None)

    assert "response" not in result
