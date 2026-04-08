"""
GCP Certificate Authority Service client (used by `function/` serverless path).

Based on: https://cloud.google.com/certificate-authority-service/docs/requesting-certificates

Certificate Request Flow:
1. CSR contains: Subject DN, Public Key, Extensions (SANs, keyUsage, etc.)
2. Lifetime/Validity is specified separately when calling CAS API
3. Certificate Template (optional) can enforce additional policies
"""
import os
import logging
import requests
from google.auth import default
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)


class CASClient:
    """Client for interacting with GCP Certificate Authority Service"""
    
    def __init__(self):
        # Load configuration from environment
        self.project_id = os.environ.get('PROJECT_ID')
        self.region = os.environ.get('REGION')
        self.ca_pool_id = os.environ.get('CA_POOL_ID')
        self.ca_id = os.environ.get('CA_ID')  # Optional: specific CA in pool
        self.certificate_template = os.environ.get('CERTIFICATE_TEMPLATE', '')
        
        # Validate required config
        if not all([self.project_id, self.region, self.ca_pool_id]):
            raise ValueError("Missing required CAS configuration: PROJECT_ID, REGION, CA_POOL_ID")
        
        # Get credentials
        self.credentials, _ = default()
        
        # API endpoint
        self.base_url = f"https://privateca.googleapis.com/v1/projects/{self.project_id}/locations/{self.region}/caPools/{self.ca_pool_id}"
        
        logger.info(f"CAS Client initialized for pool: {self.ca_pool_id}")
    
    def _get_auth_headers(self):
        """Get authenticated headers for API calls"""
        self.credentials.refresh(Request())
        return {
            "Authorization": f"Bearer {self.credentials.token}",
            "Content-Type": "application/json"
        }
    
    def issue_certificate(self, csr_pem, validity_days, certificate_id=None, validation_mode=False):
        """
        Issue a certificate using CAS REST API
        
        Args:
            csr_pem: PEM-encoded Certificate Signing Request
            validity_days: Certificate lifetime in days
            certificate_id: Optional unique ID for the certificate
            validation_mode: If True, validates request without issuing (dry-run)
        
        Returns:
            Tuple of (certificate_pem, error_message)
            - On success: (certificate_pem_string, None)
            - On failure: (None, error_message_string)
        
        Reference: https://cloud.google.com/certificate-authority-service/docs/requesting-certificates
        """
        try:
            if isinstance(csr_pem, bytes):
                csr_pem = csr_pem.decode('utf-8')
            
            # Calculate lifetime in seconds (as per CAS API requirement)
            # The lifetime is passed to CAS API, NOT embedded in CSR
            lifetime_seconds = validity_days * 24 * 60 * 60
            
            # Build certificate request body
            # CSR contains subject/key/extensions, lifetime is separate
            request_body = {
                "pemCsr": csr_pem,
                "lifetime": f"{lifetime_seconds}s"
            }
            
            # Add certificate template if specified (enforces additional policies)
            if self.certificate_template:
                template_path = f"projects/{self.project_id}/locations/{self.region}/certificateTemplates/{self.certificate_template}"
                request_body["certificateTemplate"] = template_path
                logger.info(f"Using certificate template: {self.certificate_template}")
            
            # Build request URL with query parameters
            url = f"{self.base_url}/certificates"
            query_params = []
            
            if certificate_id:
                query_params.append(f"certificateId={certificate_id}")
            
            # Optionally specify a specific CA from the pool
            if self.ca_id:
                query_params.append(f"issuingCertificateAuthorityId={self.ca_id}")
            
            # Validation mode - test without issuing
            if validation_mode:
                query_params.append("validateOnly=true")
            
            if query_params:
                url += "?" + "&".join(query_params)
            
            logger.info(f"CAS Request: POST {url}")
            logger.info(f"Lifetime: {validity_days} days ({lifetime_seconds}s)")
            
            # Make API call
            response = requests.post(
                url, 
                json=request_body, 
                headers=self._get_auth_headers(),
                timeout=30
            )
            
            # Handle response
            if response.status_code not in [200, 201]:
                error_detail = self._parse_error(response)
                logger.error(f"CAS API error: {error_detail}")
                return None, error_detail
            
            # Validation mode returns empty on success
            if validation_mode:
                logger.info("Validation mode: Request would succeed")
                return "VALIDATION_SUCCESS", None
            
            result = response.json()
            
            # Extract certificate and chain
            cert_pem = result.get("pemCertificate", "")
            cert_chain = result.get("pemCertificateChain", [])
            cert_name = result.get("name", "unknown")
            
            # Combine certificate with chain for full chain
            if cert_chain:
                full_cert = cert_pem + "\n" + "\n".join(cert_chain)
            else:
                full_cert = cert_pem
            
            logger.info(f"Certificate issued successfully: {cert_name}")
            return full_cert, None
            
        except requests.exceptions.Timeout:
            error_msg = "CAS API request timed out"
            logger.error(error_msg)
            return None, error_msg
        except requests.exceptions.RequestException as e:
            error_msg = f"CAS API connection error: {str(e)}"
            logger.error(error_msg)
            return None, error_msg
        except Exception as e:
            error_msg = f"CAS error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return None, error_msg
    
    def _parse_error(self, response):
        """Parse error response from CAS API"""
        try:
            error_data = response.json()
            if "error" in error_data:
                error = error_data["error"]
                message = error.get("message", "Unknown error")
                code = error.get("code", response.status_code)
                
                # Provide helpful messages for common errors
                if "PERMISSION_DENIED" in str(error):
                    return f"Permission denied. Ensure service account has 'CA Service Certificate Requester' role. Details: {message}"
                elif "NOT_FOUND" in str(error):
                    return f"Resource not found. Check CA pool/template exists. Details: {message}"
                elif "INVALID_ARGUMENT" in str(error):
                    return f"Invalid request. {message}"
                elif "FAILED_PRECONDITION" in str(error):
                    return f"Precondition failed (policy violation?). {message}"
                
                return f"CAS error {code}: {message}"
            return f"HTTP {response.status_code}: {response.text[:500]}"
        except:
            return f"HTTP {response.status_code}: {response.text[:500]}"
    
    def validate_certificate_request(self, csr_pem, validity_days):
        """
        Validate a certificate request without issuing (dry-run)
        
        This uses CAS validation mode to check if the request would succeed
        without actually creating a certificate.
        
        Reference: https://cloud.google.com/certificate-authority-service/docs/requesting-certificates#request_a_certificate_in_validation_mode
        """
        return self.issue_certificate(
            csr_pem=csr_pem,
            validity_days=validity_days,
            validation_mode=True
        )