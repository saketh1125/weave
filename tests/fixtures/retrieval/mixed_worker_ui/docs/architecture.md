# Architecture

The TypeScript console sends a job request to the Python API. The API places
the request on a durable queue, and a separate Python worker claims queued
jobs, records attempts, and reports completion. A short retry policy returns
temporary failures to the queue for another claim.

The browser and worker communicate only through the API and database. The
browser does not execute repository code.
