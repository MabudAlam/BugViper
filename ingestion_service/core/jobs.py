"""
Job management for the ingestion system
"""
from typing import Optional

from common.job_models import JobStatus

class JobManager:
    """Simple job manager for tracking ingestion progress."""
    
    def __init__(self):
        self.jobs = {}
        self.current_job_id = 0
    
    def create_job(self, job_type: str, **kwargs) -> str:
        """Create a new job."""
        self.current_job_id += 1
        job_id = f"job_{self.current_job_id}"
        self.jobs[job_id] = {
            "id": job_id,
            "type": job_type,
            "status": JobStatus.PENDING,
            "data": kwargs
        }
        return job_id
    
    def update_job_status(self, job_id: str, status: JobStatus):
        """Update job status."""
        if job_id in self.jobs:
            self.jobs[job_id]["status"] = status
    
    def get_job(self, job_id: str) -> Optional[dict]:
        """Get job by ID."""
        return self.jobs.get(job_id)