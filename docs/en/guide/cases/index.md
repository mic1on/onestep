---
title: User Cases | Guide
outline: deep
---

# User Cases

This section collects production-ready OneStep user cases. Cases use anonymous business names and environment variables,
focusing on reusable connector combinations, reliability boundaries, pre-deployment checks, and disaster recovery.
Business field transformations should still be implemented by the application's own Python handlers.

## Cases

- [MySQL Order Stream Incremental Sync to Feishu Bitable](/guide/cases/mysql-feishu-order-sync):
  Uses MySQL composite cursor, persistent progress, and Feishu Insert key index to reliably write
  immutable order records into a Bitable.

## How to Read

First read the prerequisites and full YAML in each case, then replace resource names, environment variables, view names, and field mappings with your own business values. The `handler` in each case only defines the input/output contract; do not put business transformation, query, or branching logic into YAML.
