"""Celery periodic task: scan geographic zones for health threats."""

# TODO: Run every 6 hours via Celery Beat
# Query AQI + public health APIs per tracked zone
# Store new threats in MongoDB, trigger fold_protein if new sequence found
