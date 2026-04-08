"""Serverless entrypoints (Google Cloud Functions–oriented; can be adapted to Cloud Run + Eventarc)."""
import os
import json
import logging
import re
import time
from datetime import datetime, timedelta
from google.cloud import storage
from validator import CSRValidator
from cas_client import CASClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

storage_client = storage.Client()
cas_client = None
validator = None


def get_validator():
    global validator
    if validator is None:
        validator = CSRValidator()
    return validator


def get_cas_client():
    global cas_client
    if cas_client is None:
        cas_client = CASClient()
    return cas_client


def extract_validity_from_filename(filename):
    """Fallback: Extract validity from filename pattern"""
    default_days = int(os.environ.get('MIN_VALIDITY_DAYS', '365'))
    
    days_match = re.search(r'\.(\d+)d\.csr$', filename.lower())
    if days_match:
        return int(days_match.group(1))
    
    years_match = re.search(r'\.(\d+)y\.csr$', filename.lower())
    if years_match:
        return int(years_match.group(1)) * 365
    
    months_match = re.search(r'\.(\d+)m\.csr$', filename.lower())
    if months_match:
        return int(months_match.group(1)) * 30
    
    return default_days


def get_request_config(bucket, file_name, max_retries=3, retry_delay=2):
    """
    Get certificate request configuration from multiple sources (in priority order):
    1. JSON metadata file (myapp.json alongside myapp.csr)
    2. GCS object metadata (x-goog-meta-validity-days)
    3. Filename pattern (myapp.400d.csr)
    4. Default from environment
    
    Includes retry logic to handle simultaneous uploads where JSON may arrive
    shortly after CSR.
    
    Returns: dict with validity_days, requested_by, purpose, warnings, etc.
    """
    default_days = int(os.environ.get('MIN_VALIDITY_DAYS', '365'))
    require_json = os.environ.get('REQUIRE_JSON_METADATA', 'false').lower() == 'true'
    
    config = {
        "validity_days": default_days,
        "source": "default",
        "requested_by": None,
        "purpose": None,
        "environment": None,
        "warnings": [],
        "errors": [],
        "metadata_file_expected": None
    }
    
    # Get base name for metadata file lookup
    base_name = os.path.basename(file_name)
    base_name = re.sub(r'\.\d+[dym]\.csr$', '', base_name, flags=re.IGNORECASE)
    base_name = re.sub(r'\.csr$', '', base_name, flags=re.IGNORECASE)
    metadata_file = f"csr-requests/{base_name}.json"
    config["metadata_file_expected"] = metadata_file
    
    # Priority 1: JSON metadata file (with retry for simultaneous uploads)
    json_found = False
    json_parse_error = None
    
    for attempt in range(max_retries):
        try:
            metadata_blob = bucket.blob(metadata_file)
            if metadata_blob.exists():
                json_found = True
                metadata_content = metadata_blob.download_as_text()
                
                try:
                    metadata = json.loads(metadata_content)
                except json.JSONDecodeError as je:
                    json_parse_error = f"Invalid JSON in {metadata_file}: {str(je)}"
                    config["errors"].append(json_parse_error)
                    logger.error(json_parse_error)
                    break
                
                # Check for required field: validity_days
                if "validity_days" not in metadata:
                    config["warnings"].append(f"Missing 'validity_days' in {metadata_file}, using default {default_days} days")
                else:
                    try:
                        config["validity_days"] = int(metadata["validity_days"])
                        config["source"] = "json_metadata"
                    except (ValueError, TypeError):
                        config["errors"].append(f"Invalid 'validity_days' value: {metadata.get('validity_days')} - must be a number")
                
                # Check for recommended fields
                if not metadata.get("requested_by"):
                    config["warnings"].append("Missing 'requested_by' field (recommended for audit)")
                else:
                    config["requested_by"] = metadata.get("requested_by")
                
                if not metadata.get("purpose"):
                    config["warnings"].append("Missing 'purpose' field (recommended for audit)")
                else:
                    config["purpose"] = metadata.get("purpose")
                
                config["environment"] = metadata.get("environment")
                
                logger.info(f"Config from JSON metadata (attempt {attempt + 1}): {config}")
                return config
            else:
                # JSON not found yet, wait and retry
                if attempt < max_retries - 1:
                    logger.info(f"JSON metadata not found, waiting {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
        except Exception as e:
            logger.warning(f"Error reading JSON metadata (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
    
    # JSON not found after retries
    if not json_found:
        missing_msg = f"No metadata file found: {metadata_file}"
        logger.warning(missing_msg)
        
        if require_json:
            config["errors"].append(f"REQUIRED: {missing_msg} - JSON metadata file is mandatory")
        else:
            config["warnings"].append(f"{missing_msg} - using fallback methods")
    
    # Priority 2: GCS object metadata
    try:
        csr_blob = bucket.blob(file_name)
        csr_blob.reload()  # Fetch metadata
        
        if csr_blob.metadata:
            if "validity-days" in csr_blob.metadata:
                config["validity_days"] = int(csr_blob.metadata["validity-days"])
                config["source"] = "gcs_metadata"
            
            config["requested_by"] = csr_blob.metadata.get("requested-by")
            config["purpose"] = csr_blob.metadata.get("purpose")
            config["environment"] = csr_blob.metadata.get("environment")
            
            if config["source"] == "gcs_metadata":
                logger.info(f"Config from GCS metadata: {config}")
                return config
    except Exception as e:
        logger.debug(f"No GCS metadata: {e}")
    
    # Priority 3: Filename pattern
    filename_days = extract_validity_from_filename(file_name)
    if filename_days != default_days:
        config["validity_days"] = filename_days
        config["source"] = "filename"
        logger.info(f"Config from filename: {config}")
        return config
    
    # Priority 4: Default
    logger.info(f"Using default config: {config}")
    return config


def save_certificate(bucket_name, base_filename, cert_pem):
    bucket = storage_client.bucket(bucket_name)
    cert_blob = bucket.blob(f"certificates/{base_filename}.crt")
    cert_blob.upload_from_string(cert_pem, content_type='application/x-pem-file')
    logger.info(f"Certificate saved: certificates/{base_filename}.crt")


def get_report_styles(is_success=True):
    """Clean, professional styles"""
    status_color = '#0d6f3f' if is_success else '#b91c1c'
    return f'''
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; min-height: 100vh; padding: 40px 20px; color: #1a1a1a; }}
        .report {{ max-width: 600px; margin: 0 auto; background: #fff; border: 1px solid #e0e0e0; }}
        .header {{ padding: 28px 32px; border-bottom: 1px solid #e0e0e0; }}
        .status-badge {{ display: inline-block; padding: 6px 14px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; border-radius: 2px; margin-bottom: 16px; background: {status_color}; color: #fff; }}
        .header h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 4px; color: #1a1a1a; }}
        .header p {{ font-size: 13px; color: #666; }}
        .body {{ padding: 28px 32px; }}
        .section {{ margin-bottom: 28px; }}
        .section:last-child {{ margin-bottom: 0; }}
        .section-title {{ font-size: 10px; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 16px; }}
        .info-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
        .info-item {{ padding: 12px 0; border-bottom: 1px solid #f0f0f0; }}
        .info-item label {{ display: block; font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
        .info-item span {{ font-size: 14px; font-weight: 500; color: #1a1a1a; }}
        table {{ width: 100%; border-collapse: collapse; border: 1px solid #e0e0e0; }}
        th, td {{ padding: 12px 16px; text-align: left; font-size: 13px; }}
        th {{ font-weight: 600; color: #666; background: #fafafa; border-bottom: 1px solid #e0e0e0; }}
        td {{ border-bottom: 1px solid #f0f0f0; }}
        tr:last-child td {{ border-bottom: none; }}
        .pass {{ color: #0d6f3f; font-weight: 600; }}
        .fail {{ color: #b91c1c; font-weight: 600; }}
        .rec {{ background: #f8fdf8; border: 1px solid #d4e8d4; padding: 16px; margin-top: 24px; }}
        .rec-label {{ font-size: 10px; font-weight: 600; color: #0d6f3f; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }}
        .rec-text {{ font-size: 14px; color: #1a1a1a; }}
        .rec-text strong {{ font-weight: 600; }}
        .download {{ background: #fafafa; border: 1px solid #e0e0e0; padding: 16px; text-align: center; margin-top: 24px; }}
        .download p {{ font-size: 13px; color: #666; margin: 0; }}
        .download code {{ font-family: 'SF Mono', Monaco, Consolas, monospace; font-size: 12px; color: #1a1a1a; }}
        .footer {{ font-size: 11px; color: #999; text-align: right; padding: 16px 32px; border-top: 1px solid #e0e0e0; background: #fafafa; }}
    '''


def generate_error_html(base_filename, result, requested_days, additional_error=None, config_source="default", config_warnings=None, config_errors=None):
    """Generate professional HTML error report"""
    
    now = datetime.utcnow()
    expiry_date = now + timedelta(days=requested_days)
    
    min_days = int(os.environ.get('MIN_VALIDITY_DAYS', '365'))
    max_days = int(os.environ.get('MAX_VALIDITY_DAYS', '730'))
    
    # Determine rule status
    min_pass = requested_days >= min_days
    max_pass = requested_days <= max_days
    month, day = expiry_date.month, expiry_date.day
    blackout_pass = not ((month >= 10) or (month == 1 and day <= 15))
    
    # Get CSR info
    cn = "-"
    key_info = "-"
    org = "-"
    if result and result.csr_info:
        if "subject" in result.csr_info:
            cn = result.csr_info["subject"].get("commonName", "-")
            org = result.csr_info["subject"].get("organizationName", "-")
        if "key_info" in result.csr_info:
            ki = result.csr_info["key_info"]
            key_info = f"{ki.get('type', '')} {ki.get('size', '')}".strip() or "-"
    
    # Build recommendation
    rec_html = ""
    if result and result.recommended_validity_days:
        rec_expiry = now + timedelta(days=result.recommended_validity_days)
        rec_html = f'''
            <div class="rec">
                <div class="rec-label">Recommendation</div>
                <div class="rec-text">Use <strong>{result.recommended_validity_days} days</strong> (expires {rec_expiry.strftime('%b %d, %Y')})</div>
            </div>'''
    
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Certificate Request - {base_filename}</title>
    <style>{get_report_styles(is_success=False)}</style>
</head>
<body>
    <div class="report">
        <div class="header">
            <div class="status-badge">Rejected</div>
            <h1>Certificate Request</h1>
            <p>{base_filename}</p>
        </div>
        <div class="body">
            <div class="section">
                <div class="section-title">Request Details</div>
                <div class="info-grid">
                    <div class="info-item">
                        <label>Common Name</label>
                        <span>{cn}</span>
                    </div>
                    <div class="info-item">
                        <label>Organization</label>
                        <span>{org}</span>
                    </div>
                    <div class="info-item">
                        <label>Validity Requested</label>
                        <span>{requested_days} days</span>
                    </div>
                    <div class="info-item">
                        <label>Expiry Date</label>
                        <span>{expiry_date.strftime('%b %d, %Y')}</span>
                    </div>
                    <div class="info-item">
                        <label>Key Type</label>
                        <span>{key_info}</span>
                    </div>
                    <div class="info-item">
                        <label>Allowed Range</label>
                        <span>{min_days} - {max_days} days</span>
                    </div>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">Policy Validation</div>
                <table>
                    <tr>
                        <th>Policy</th>
                        <th style="text-align:right">Status</th>
                    </tr>
                    <tr>
                        <td>Minimum validity (≥ {min_days} days)</td>
                        <td style="text-align:right" class="{'pass' if min_pass else 'fail'}">{'Pass' if min_pass else 'Fail'}</td>
                    </tr>
                    <tr>
                        <td>Maximum validity (≤ {max_days} days)</td>
                        <td style="text-align:right" class="{'pass' if max_pass else 'fail'}">{'Pass' if max_pass else 'Fail'}</td>
                    </tr>
                    <tr>
                        <td>Blackout period (Oct 1 - Jan 15)</td>
                        <td style="text-align:right" class="{'pass' if blackout_pass else 'fail'}">{'Pass' if blackout_pass else 'Fail'}</td>
                    </tr>
                </table>
            </div>
            {rec_html}
        </div>
        <div class="footer">
            {now.strftime('%Y-%m-%d %H:%M UTC')}
        </div>
    </div>
</body>
</html>'''
    
    return html


def save_error_report(bucket_name, base_filename, result, requested_days, additional_error=None, config_source="default", config_warnings=None, config_errors=None):
    """Save validation error report"""
    bucket = storage_client.bucket(bucket_name)
    
    # Save HTML report
    html_content = generate_error_html(base_filename, result, requested_days, additional_error, config_source, config_warnings, config_errors)
    html_blob = bucket.blob(f"errors/{base_filename}.html")
    html_blob.upload_from_string(html_content, content_type='text/html')
    logger.info(f"Report saved: errors/{base_filename}.html")


def generate_success_html(base_filename, requested_days, cert_id, csr_info=None):
    """Generate professional success report with policy validation"""
    now = datetime.utcnow()
    expiry_date = now + timedelta(days=requested_days)
    
    min_days = int(os.environ.get('MIN_VALIDITY_DAYS', '365'))
    max_days = int(os.environ.get('MAX_VALIDITY_DAYS', '730'))
    
    # Get CSR info
    cn = "-"
    org = "-"
    key_info = "-"
    if csr_info:
        if "subject" in csr_info:
            cn = csr_info["subject"].get("commonName", "-")
            org = csr_info["subject"].get("organizationName", "-")
        if "key_info" in csr_info:
            ki = csr_info["key_info"]
            key_info = f"{ki.get('type', '')} {ki.get('size', '')}".strip() or "-"
    
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Certificate Issued - {base_filename}</title>
    <style>{get_report_styles(is_success=True)}</style>
</head>
<body>
    <div class="report">
        <div class="header">
            <div class="status-badge">Issued</div>
            <h1>Certificate Request</h1>
            <p>{base_filename}</p>
        </div>
        <div class="body">
            <div class="section">
                <div class="section-title">Certificate Details</div>
                <div class="info-grid">
                    <div class="info-item">
                        <label>Common Name</label>
                        <span>{cn}</span>
                    </div>
                    <div class="info-item">
                        <label>Organization</label>
                        <span>{org}</span>
                    </div>
                    <div class="info-item">
                        <label>Validity</label>
                        <span>{requested_days} days</span>
                    </div>
                    <div class="info-item">
                        <label>Key Type</label>
                        <span>{key_info}</span>
                    </div>
                    <div class="info-item">
                        <label>Issued Date</label>
                        <span>{now.strftime('%b %d, %Y')}</span>
                    </div>
                    <div class="info-item">
                        <label>Expiry Date</label>
                        <span>{expiry_date.strftime('%b %d, %Y')}</span>
                    </div>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">Policy Validation</div>
                <table>
                    <tr>
                        <th>Policy</th>
                        <th style="text-align:right">Status</th>
                    </tr>
                    <tr>
                        <td>Minimum validity (≥ {min_days} days)</td>
                        <td style="text-align:right" class="pass">Pass</td>
                    </tr>
                    <tr>
                        <td>Maximum validity (≤ {max_days} days)</td>
                        <td style="text-align:right" class="pass">Pass</td>
                    </tr>
                    <tr>
                        <td>Blackout period (Oct 1 - Jan 15)</td>
                        <td style="text-align:right" class="pass">Pass</td>
                    </tr>
                </table>
            </div>
            
            <div class="download">
                <p>Certificate file: <code>certificates/{base_filename}.crt</code></p>
            </div>
        </div>
        <div class="footer">
            {now.strftime('%Y-%m-%d %H:%M UTC')}
        </div>
    </div>
</body>
</html>'''
    return html


def save_success_report(bucket_name, base_filename, requested_days, cert_id, csr_info=None):
    """Save success report"""
    bucket = storage_client.bucket(bucket_name)
    html_content = generate_success_html(base_filename, requested_days, cert_id, csr_info)
    html_blob = bucket.blob(f"certificates/{base_filename}-report.html")
    html_blob.upload_from_string(html_content, content_type='text/html')
    logger.info(f"Report saved: certificates/{base_filename}-report.html")


def process_csr(event, context):
    """Cloud Function entry point - triggered by Cloud Storage"""
    try:
        bucket_name = event['bucket']
        file_name = event['name']
        
        logger.info(f"Processing: gs://{bucket_name}/{file_name}")
        
        if not file_name.startswith("csr-requests/"):
            logger.info("Skipping: not in csr-requests/")
            return
        
        if not file_name.lower().endswith(".csr"):
            logger.info("Skipping: not a .csr file")
            return
        
        if file_name.endswith(".keep"):
            return
        
        base_filename = os.path.basename(file_name)
        base_filename = re.sub(r'\.\d+[dym]\.csr$', '', base_filename, flags=re.IGNORECASE)
        base_filename = re.sub(r'\.csr$', '', base_filename, flags=re.IGNORECASE)
        
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        csr_content = blob.download_as_bytes()
        
        # Get config from metadata file, GCS metadata, or filename
        request_config = get_request_config(bucket, file_name)
        validity_days = request_config["validity_days"]
        config_warnings = request_config.get("warnings", [])
        config_errors = request_config.get("errors", [])
        
        logger.info(f"Requested validity: {validity_days} days (source: {request_config['source']})")
        if config_warnings:
            logger.warning(f"Config warnings: {config_warnings}")
        if config_errors:
            logger.error(f"Config errors: {config_errors}")
        
        # If there are config errors (e.g., JSON required but missing), fail early
        if config_errors:
            # Create a minimal result for the error report
            validator_instance = get_validator()
            result = validator_instance.validate(csr_content, validity_days)
            save_error_report(
                bucket_name, base_filename, result, validity_days,
                additional_error="Configuration errors prevented processing",
                config_source=request_config["source"],
                config_warnings=config_warnings,
                config_errors=config_errors
            )
            return
        
        validator_instance = get_validator()
        result = validator_instance.validate(csr_content, validity_days)
        
        logger.info(f"Validation: valid={result.is_valid}, errors={result.errors}, csr_errors={result.csr_errors}")
        
        if not result.is_valid:
            save_error_report(
                bucket_name, base_filename, result, validity_days,
                config_source=request_config["source"],
                config_warnings=config_warnings,
                config_errors=config_errors
            )
            return
        
        logger.info("Validation passed, issuing certificate...")
        
        cert_id = f"cert-{base_filename}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        
        cas = get_cas_client()
        cert_pem, error = cas.issue_certificate(
            csr_pem=csr_content.decode('utf-8'),
            validity_days=validity_days,
            certificate_id=cert_id
        )
        
        if error:
            save_error_report(
                bucket_name, base_filename, result, validity_days,
                additional_error=f"CAS Error: {error}",
                config_source=request_config["source"],
                config_warnings=config_warnings,
                config_errors=config_errors
            )
            return
        
        save_certificate(bucket_name, base_filename, cert_pem)
        save_success_report(bucket_name, base_filename, validity_days, cert_id, result.csr_info)
        logger.info(f"Certificate issued: {cert_id}")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        raise
