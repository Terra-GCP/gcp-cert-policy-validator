"""
GCP Certificate Policy Validator — optional Python CSR checks

Validates CSRs and certificate request parameters against organizational policies.

CSR (Certificate Signing Request) contains:
- Subject DN (CN, O, OU, C, ST, L, etc.)
- Public Key (RSA, EC, etc.)
- Extensions (SANs, keyUsage, extendedKeyUsage)

Certificate lifetime/validity is NOT in CSR - it's specified when requesting
the certificate from the CA.

Reference: https://cloud.google.com/certificate-authority-service/docs/requesting-certificates
"""
import os
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa, ec, ed25519, ed448
from cryptography.x509.oid import NameOID, ExtensionOID

logger = logging.getLogger(__name__)


@dataclass
class ValidationConfig:
    """Configuration for certificate request validation"""
    # Validity period constraints
    min_validity_days: int = 365
    max_validity_days: int = 730
    
    # Blackout period (no certificate expiry allowed)
    blackout_start_month: int = 10
    blackout_start_day: int = 1
    blackout_end_month: int = 1
    blackout_end_day: int = 15
    
    # Key strength requirements (based on Google Cloud recommendations)
    # Reference: https://cloud.google.com/certificate-authority-service/docs/best-practices
    min_rsa_key_size: int = 2048
    min_ec_key_size: int = 256  # P-256 or higher
    allowed_key_types: tuple = ('RSA', 'EC', 'Ed25519', 'Ed448')

    @classmethod
    def from_env(cls):
        return cls(
            min_validity_days=int(os.environ.get('MIN_VALIDITY_DAYS', '365')),
            max_validity_days=int(os.environ.get('MAX_VALIDITY_DAYS', '730')),
            blackout_start_month=int(os.environ.get('BLACKOUT_START_MONTH', '10')),
            blackout_start_day=int(os.environ.get('BLACKOUT_START_DAY', '1')),
            blackout_end_month=int(os.environ.get('BLACKOUT_END_MONTH', '1')),
            blackout_end_day=int(os.environ.get('BLACKOUT_END_DAY', '15')),
            min_rsa_key_size=int(os.environ.get('MIN_RSA_KEY_SIZE', '2048')),
            min_ec_key_size=int(os.environ.get('MIN_EC_KEY_SIZE', '256'))
        )


@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    csr_info: Dict[str, Any] = field(default_factory=dict)
    csr_errors: List[str] = field(default_factory=list)
    recommended_validity_days: Optional[int] = None
    recommendation_reason: str = ""

    def to_dict(self):
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "csr_info": self.csr_info,
            "csr_errors": self.csr_errors,
            "recommended_validity_days": self.recommended_validity_days,
            "recommendation_reason": self.recommendation_reason
        }


class CSRValidator:
    # Required and optional subject fields
    REQUIRED_FIELDS = ['commonName']
    RECOMMENDED_FIELDS = ['organizationName', 'countryName']
    
    # OID mapping for readable names
    OID_NAMES = {
        NameOID.COMMON_NAME: 'commonName',
        NameOID.ORGANIZATION_NAME: 'organizationName',
        NameOID.ORGANIZATIONAL_UNIT_NAME: 'organizationalUnitName',
        NameOID.COUNTRY_NAME: 'countryName',
        NameOID.STATE_OR_PROVINCE_NAME: 'stateOrProvinceName',
        NameOID.LOCALITY_NAME: 'localityName',
        NameOID.EMAIL_ADDRESS: 'emailAddress',
    }

    def __init__(self, config=None):
        self.config = config or ValidationConfig.from_env()
        logger.info(f"Validator config: {self.config}")

    def parse_csr(self, csr_pem):
        """Parse CSR and return detailed info or errors"""
        csr_errors = []
        csr_info = {"subject": {}, "extensions": [], "signature_algorithm": None, "key_info": {}}
        
        # Handle bytes/string
        if isinstance(csr_pem, str):
            csr_pem = csr_pem.encode('utf-8')
        
        # Check if content looks like a CSR
        csr_text = csr_pem.decode('utf-8', errors='replace').strip()
        
        if not csr_text:
            return None, csr_info, ["CSR file is empty"]
        
        if "-----BEGIN CERTIFICATE REQUEST-----" not in csr_text:
            if "-----BEGIN NEW CERTIFICATE REQUEST-----" in csr_text:
                # Some tools use this header
                pass
            elif "-----BEGIN CERTIFICATE-----" in csr_text:
                return None, csr_info, ["File contains a certificate, not a CSR (Certificate Signing Request)"]
            elif "-----BEGIN" in csr_text:
                return None, csr_info, ["Invalid PEM format - not a CSR. Found different PEM type."]
            else:
                return None, csr_info, ["Invalid CSR format - missing PEM headers (-----BEGIN CERTIFICATE REQUEST-----)"]
        
        if "-----END CERTIFICATE REQUEST-----" not in csr_text and "-----END NEW CERTIFICATE REQUEST-----" not in csr_text:
            return None, csr_info, ["Invalid CSR format - missing PEM footer (-----END CERTIFICATE REQUEST-----)"]
        
        try:
            csr = x509.load_pem_x509_csr(csr_pem)
        except Exception as e:
            error_msg = str(e)
            if "Unable to load" in error_msg:
                csr_errors.append("CSR data is corrupted or malformed")
            elif "base64" in error_msg.lower():
                csr_errors.append("Invalid base64 encoding in CSR")
            else:
                csr_errors.append(f"Failed to parse CSR: {error_msg}")
            return None, csr_info, csr_errors
        
        # Verify CSR signature
        try:
            if not csr.is_signature_valid:
                csr_errors.append("CSR signature verification failed - CSR may be tampered")
        except Exception:
            csr_errors.append("Could not verify CSR signature")
        
        # Extract subject fields
        for attr in csr.subject:
            oid_name = self.OID_NAMES.get(attr.oid, attr.oid.dotted_string)
            csr_info["subject"][oid_name] = attr.value
        
        # Check required fields
        for req_field in self.REQUIRED_FIELDS:
            if req_field not in csr_info["subject"] or not csr_info["subject"][req_field]:
                csr_errors.append(f"Missing required field: {req_field}")
        
        # Check recommended fields (warnings, not errors)
        warnings = []
        for rec_field in self.RECOMMENDED_FIELDS:
            if rec_field not in csr_info["subject"]:
                warnings.append(f"Missing recommended field: {rec_field}")
        
        # Get key info and validate key strength
        try:
            public_key = csr.public_key()
            
            if isinstance(public_key, rsa.RSAPublicKey):
                csr_info["key_info"]["type"] = "RSA"
                csr_info["key_info"]["size"] = public_key.key_size
                if public_key.key_size < self.config.min_rsa_key_size:
                    csr_errors.append(f"RSA key size {public_key.key_size} bits is below minimum {self.config.min_rsa_key_size} bits")
            
            elif isinstance(public_key, ec.EllipticCurvePublicKey):
                csr_info["key_info"]["type"] = "EC"
                csr_info["key_info"]["curve"] = public_key.curve.name
                csr_info["key_info"]["size"] = public_key.key_size
                if public_key.key_size < self.config.min_ec_key_size:
                    csr_errors.append(f"EC key size {public_key.key_size} bits is below minimum {self.config.min_ec_key_size} bits")
            
            elif isinstance(public_key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
                csr_info["key_info"]["type"] = "EdDSA"
                csr_info["key_info"]["algorithm"] = type(public_key).__name__
            
            else:
                key_type = type(public_key).__name__
                csr_info["key_info"]["type"] = key_type
                csr_errors.append(f"Unsupported key type: {key_type}")
                
        except Exception as e:
            csr_info["key_info"]["type"] = "Unknown"
            csr_errors.append(f"Could not parse public key: {str(e)}")
        
        # Get signature algorithm
        try:
            csr_info["signature_algorithm"] = csr.signature_algorithm_oid._name
        except Exception:
            csr_info["signature_algorithm"] = "Unknown"
        
        # Extract extensions if any
        try:
            for ext in csr.extensions:
                ext_name = ext.oid._name if hasattr(ext.oid, '_name') else str(ext.oid)
                csr_info["extensions"].append(ext_name)
        except Exception:
            pass
        
        csr_info["warnings"] = warnings
        return csr, csr_info, csr_errors

    def is_in_blackout_period(self, expiry_date):
        """Check if expiry date falls in blackout period (Oct 1 - Jan 15)"""
        month, day = expiry_date.month, expiry_date.day
        
        # Oct (10), Nov (11), Dec (12)
        if month >= self.config.blackout_start_month:
            return True, f"Oct 1 - Dec 31"
        
        # Jan 1-15
        if month == self.config.blackout_end_month and day <= self.config.blackout_end_day:
            return True, f"Jan 1 - Jan 15"
        
        return False, ""

    def calculate_recommendation(self, requested_days, start_date, failed_rules):
        """Calculate smart recommendation based on which rules failed"""
        
        # If CSR has errors, no validity recommendation
        if "csr_error" in failed_rules:
            return None, ""
        
        now = start_date or datetime.utcnow()
        
        # Case 1: Below minimum
        if "min_validity" in failed_rules:
            # Simply recommend minimum
            rec_days = self.config.min_validity_days
            expiry = now + timedelta(days=rec_days)
            
            # But check if minimum hits blackout
            in_blackout, _ = self.is_in_blackout_period(expiry)
            if in_blackout:
                # Push past blackout
                rec_days = self._find_safe_days(now, self.config.min_validity_days)
            
            return rec_days, f"Increase to minimum {self.config.min_validity_days} days"
        
        # Case 2: Above maximum
        if "max_validity" in failed_rules:
            rec_days = self.config.max_validity_days
            expiry = now + timedelta(days=rec_days)
            
            # Check if max hits blackout
            in_blackout, _ = self.is_in_blackout_period(expiry)
            if in_blackout:
                rec_days = self._find_safe_days(now, self.config.max_validity_days, going_down=True)
            
            return rec_days, f"Reduce to maximum {self.config.max_validity_days} days"
        
        # Case 3: Blackout period only
        if "blackout" in failed_rules:
            # Find nearest safe date
            rec_days = self._find_safe_days(now, requested_days)
            return rec_days, "Adjust to avoid blackout period"
        
        return None, ""

    def _find_safe_days(self, start_date, requested_days, going_down=False):
        """Find nearest validity that avoids blackout"""
        expiry = start_date + timedelta(days=requested_days)
        
        # Try to push past Jan 15
        if not going_down:
            target_year = expiry.year
            if expiry.month >= self.config.blackout_start_month:
                target_year += 1
            
            jan_16 = datetime(target_year, 1, 16)
            new_days = (jan_16 - start_date).days
            
            if new_days <= self.config.max_validity_days:
                return new_days
        
        # Try to pull back to Sep 30
        target_year = expiry.year
        if expiry.month < self.config.blackout_start_month:
            target_year -= 1
        
        sep_30 = datetime(target_year, 9, 30)
        if sep_30 > start_date:
            new_days = (sep_30 - start_date).days
            if new_days >= self.config.min_validity_days:
                return new_days
        
        # Fallback: push to next year's Jan 16
        jan_16_next = datetime(expiry.year + 1, 1, 16)
        return (jan_16_next - start_date).days

    def validate(self, csr_pem, requested_validity_days=None):
        """Validate CSR and requested validity"""
        errors = []
        failed_rules = set()
        
        if requested_validity_days is None:
            requested_validity_days = self.config.min_validity_days
        
        # Parse CSR first
        csr, csr_info, csr_errors = self.parse_csr(csr_pem)
        
        if csr_errors:
            failed_rules.add("csr_error")
            return ValidationResult(
                is_valid=False,
                errors=errors,
                csr_errors=csr_errors,
                csr_info=csr_info,
                recommended_validity_days=None,
                recommendation_reason="Fix CSR errors first"
            )
        
        # Rule 1: Minimum validity
        if requested_validity_days < self.config.min_validity_days:
            errors.append(f"Validity {requested_validity_days} days is below minimum {self.config.min_validity_days} days")
            failed_rules.add("min_validity")
        
        # Rule 2: Maximum validity
        if requested_validity_days > self.config.max_validity_days:
            errors.append(f"Validity {requested_validity_days} days exceeds maximum {self.config.max_validity_days} days")
            failed_rules.add("max_validity")
        
        # Rule 3: Blackout period
        start_date = datetime.utcnow()
        expiry_date = start_date + timedelta(days=requested_validity_days)
        in_blackout, blackout_range = self.is_in_blackout_period(expiry_date)
        
        if in_blackout:
            errors.append(f"Expiry {expiry_date.strftime('%Y-%m-%d')} falls in blackout period ({blackout_range})")
            failed_rules.add("blackout")
        
        # Calculate smart recommendation
        rec_days, rec_reason = self.calculate_recommendation(requested_validity_days, start_date, failed_rules)
        
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=csr_info.get("warnings", []),
            csr_info=csr_info,
            csr_errors=[],
            recommended_validity_days=rec_days,
            recommendation_reason=rec_reason
        )
