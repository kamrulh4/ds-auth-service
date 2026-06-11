import os
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List
from google.cloud import firestore
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

logger = logging.getLogger(__name__)

class SubscriptionPlan(BaseModel):
    plan_id: str
    product_name: str
    display_name: str
    max_conversions: Optional[int]
    max_file_size_mb: int
    default_duration_days: int
    doc_id: Optional[str] = None
    paypal_link: Optional[str] = None

DEFAULT_PLAN_ID = "free_trial"
SUBSCRIPTION_PLANS = {
    "free_trial": SubscriptionPlan(
        plan_id="free_trial",
        product_name="Dataryx Free",
        display_name="Free Trial",
        max_conversions=20,
        max_file_size_mb=100,
        default_duration_days=90,
    ),
    "pro_monthly": SubscriptionPlan(
        plan_id="pro_monthly",
        product_name="Dataryx Pro",
        display_name="Pro Monthly",
        max_conversions=None,
        max_file_size_mb=1024,
        default_duration_days=30,
        paypal_link="https://www.paypal.com/ncp/payment/7HSHNZT23E6P6"
    )
}

class FirestoreService:
    def __init__(self, client: Optional[firestore.Client] = None):
        self.client = client or self._create_client()
        self.collection_name = "payments"  # Firestore path: payments/Dataryx/<email>
        self.doc_id = "Dataryx"

    def _create_client(self) -> firestore.Client:
        try:
            # Load private key and key ID from environment variables
            private_key_id = os.getenv("FIRESTORE_PRIVATE_KEY_ID")
            private_key = os.getenv("FIRESTORE_PRIVATE_KEY")
            
            if not private_key_id or not private_key:
                logger.error("FIRESTORE_PRIVATE_KEY_ID or FIRESTORE_PRIVATE_KEY is missing from environment variables.")
                # Fallback to default credentials from environment if available
                if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                    logger.info("Initializing Firestore with standard GOOGLE_APPLICATION_CREDENTIALS...")
                    return firestore.Client()
                return None
            
            # Strip any surrounding quotes that may be preserved by Docker env file loaders
            private_key = private_key.strip()
            if private_key.startswith('"') and private_key.endswith('"'):
                private_key = private_key[1:-1].strip()
            elif private_key.startswith("'") and private_key.endswith("'"):
                private_key = private_key[1:-1].strip()

            # Format the private key to replace escaped literal '\n' or '\\n' with actual newlines
            formatted_private_key = private_key.replace("\\\\n", "\n").replace("\\n", "\n")
            
            key_data = {
                "type": "service_account",
                "project_id": os.getenv("FIRESTORE_PROJECT_ID", "payment-enterprise"),
                "private_key_id": private_key_id,
                "private_key": formatted_private_key,
                "client_email": os.getenv("FIRESTORE_CLIENT_EMAIL", "payment-enterprise@appspot.gserviceaccount.com"),
                "client_id": os.getenv("FIRESTORE_CLIENT_ID", "115840911015379735940"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": os.getenv("FIRESTORE_CLIENT_CERT_URL", "https://www.googleapis.com/robot/v1/metadata/x509/payment-enterprise%40appspot.gserviceaccount.com"),
                "universe_domain": "googleapis.com"
            }
            
            logger.info("Initializing Firestore client using environment credentials...")
            try:
                creds = service_account.Credentials.from_service_account_info(
                    key_data, 
                    scopes=['https://www.googleapis.com/auth/cloud-platform']
                )
                auth_request = Request()
                creds.refresh(auth_request)
                return firestore.Client(credentials=creds, project=creds.project_id)
            except Exception as e:
                logger.error(f"Firestore Auth Error: {e}", exc_info=True)
                return None
        except Exception as exc:
            logger.error(f"Fatal error creating Firestore client: {exc}", exc_info=True)
            return None

    def get_subscription(self, email: str) -> Optional[Dict[str, Any]]:
        try:
            if not self.client: return None
            doc_ref = self.client.collection(self.collection_name).document(self.doc_id)
            snapshot = doc_ref.get(timeout=30)
            if not snapshot.exists: return None
            data = snapshot.to_dict() or {}
            return data.get(email.lower())
        except Exception as e:
            logger.error(f"Firestore Get Error: {e}")
            return None

    def ensure_default_subscription(self, email: str) -> bool:
        try:
            if not self.client: return False
            existing = self.get_subscription(email)
            if existing: return True

            plan = SUBSCRIPTION_PLANS[DEFAULT_PLAN_ID]
            expiry = (datetime.utcnow() + timedelta(days=plan.default_duration_days)).date().isoformat()
            
            payload = {
                "product": plan.product_name,
                "expiry": expiry,
                "conversions": plan.max_conversions,
            }
            doc_ref = self.client.collection(self.collection_name).document(self.doc_id)
            doc_ref.set({email.lower(): payload}, merge=True)
            return True
        except Exception as e:
            logger.error(f"Firestore Create Error: {e}")
            return False

    def decrement_run(self, email: str) -> bool:
        try:
            if not self.client: return False
            doc_ref = self.client.collection(self.collection_name).document(self.doc_id)
            
            @firestore.transactional
            def _decrement(transaction):
                snapshot = doc_ref.get(transaction=transaction)
                data = snapshot.to_dict() or {}
                entry = data.get(email.lower())
                if not entry: return False
                
                current_conversions = entry.get("conversions")
                if current_conversions is not None:
                    if current_conversions <= 0: return False
                    entry["conversions"] = current_conversions - 1
                    transaction.set(doc_ref, {email.lower(): entry}, merge=True)
                return True

            return _decrement(self.client.transaction())
        except Exception as e:
            logger.error(f"Firestore Decrement Error: {e}")
            return False

firestore_service = FirestoreService()
