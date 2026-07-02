from sqlalchemy.orm import Session

from vinted_monitor.db.models import ActionRequest

SUPPORTED_ACTIONS = {"view", "favorite", "prepare_purchase", "purchase"}


def create_action_request(
    db: Session,
    item_id: int,
    action_type: str,
    payload: dict,
) -> ActionRequest:
    if action_type not in SUPPORTED_ACTIONS:
        raise ValueError(f"Unsupported action type: {action_type}")

    action = ActionRequest(item_id=item_id, action_type=action_type, payload=payload)
    db.add(action)
    db.commit()
    db.refresh(action)
    return action
