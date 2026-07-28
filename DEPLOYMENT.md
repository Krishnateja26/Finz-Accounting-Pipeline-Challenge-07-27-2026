# Deployment Guide

GitHub should be used as the private source-code repository. The live website should be hosted on an application platform such as Render, Railway, Fly.io, Azure App Service, Google Cloud Run, or AWS. GitHub Pages is not suitable because this is a FastAPI server app, not a static site.

## Required Environment Variables

Set these in the hosting provider dashboard. Do not commit real values.

```env
APP_NAME=Finz Accounting Pipeline
ENVIRONMENT=production
SECRET_KEY=<random-secret>
MONGO_URI=<MongoDB Atlas URI>
MONGO_DB_NAME=finz_accounting
GEMINI_API_KEY=<Gemini API key>
GEMINI_MODEL=gemini-3.6-flash
GEMINI_TIMEOUT_SECONDS=15
ADMIN_RESET_TOKEN=<long random reset token>
QBO_CLIENT_ID=<Intuit development or production client id>
QBO_CLIENT_SECRET=<Intuit client secret>
QBO_REDIRECT_URI=https://YOUR_DEPLOYED_HOST/api/quickbooks/callback
QBO_ENVIRONMENT=sandbox
```

## Render

This repository includes `render.yaml`.

1. Push the private GitHub repository.
2. In Render, create a new Blueprint or Web Service from the repo.
3. Add the environment variables above.
4. Deploy.
5. In the Intuit Developer app, add the deployed redirect URI:

```text
https://YOUR_DEPLOYED_HOST/api/quickbooks/callback
```

6. Open `/reconciliation` and reconnect QuickBooks.

## Resetting a Deployed Test Run

Use this only for sandbox/demo resets. Keep `ADMIN_RESET_TOKEN` private.

1. Reset the Intuit QuickBooks sandbox company.
2. Open `/reconciliation` in the deployed app.
3. Click `Reset app data`.
4. Enter the private `ADMIN_RESET_TOKEN`.
5. Reconnect QuickBooks.
6. Upload/import the source file again.
7. Approve classified canonical transactions.
8. Set up chart of accounts.
9. Sync approved transactions.
10. Run reconciliation.

The reset clears uploaded rows, normalized transactions, learned rules, sync logs, reconciliation runs, and the saved QuickBooks OAuth connection. It also rotates the app's sync namespace so QuickBooks does not reuse old idempotency request IDs.

## Railway

1. Create a new Railway project from the GitHub repo.
2. Set the same environment variables.
3. Use this start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

4. Add the Railway callback URL to Intuit redirect URIs.

## Security Checklist Before Deploying

- Rotate the Gemini key that appeared in local browser error output.
- Rotate the Intuit/QBO client secret if it was shown in any terminal/chat output.
- Confirm `.env` is ignored by git.
- Store secrets only in the hosting provider environment settings.
- Use a fresh MongoDB Atlas user/password for deployment if possible.
- Keep `ADMIN_RESET_TOKEN` long, private, and different from other secrets.
