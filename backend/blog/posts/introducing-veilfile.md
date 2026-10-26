---
title: Introducing Veilfile
date: 2026-10-05
slug: introducing-veilfile
description: AI agents need somewhere private to put files. The options are public by default. Veilfile is the private alternative we built.
---

Agents stash files constantly. One saves a screenshot of what it just built. Another saves a log bundle from a run that failed at 2 a.m. A third saves a HAR file from a debugging session. The file needs to go somewhere a human can open it, and the agent has about three bad options. It can post a public gist. It can commit to a repo where the file probably does not belong. It can upload to a chat, where the file lives in scrollback forever.

On September 29, 2026, Glow Labs disclosed the PixelLeak incident, which made the cost concrete: AI coding agents had left more than 13,000 internal images in public GitHub repositories. Private screenshots, meant for someone's eyes only, sat in the open because the tooling made public the path of least resistance. That is the failure mode of the current options. The options are public by default, and then everyone acts surprised.

We are building Veilfile because the private option should be the easy one. You upload through a REST API or an MCP server. You get back a URL that is unguessable and expires on its own. Only someone who holds the link can see the file. When the time runs out, the file is deleted. Deletion is automatic and cannot be undone.

It is built for agents from the start, not adapted to them. The agent guide lives at veilfile.com/llms.txt and is written for machine readers. Keys belong to your account. A key can revoke itself in one call, and you can revoke any key from the dashboard. Expiry is set by your plan: seven days on the Free plan, up to a full year on paid plans.

A file host open to the public has to take abuse seriously, so the screening runs before anything is stored. Each upload needs an allowed file extension, and its contents are checked against that type. Secrets pasted into uploads by accident get flagged. We run a monitored abuse@ address with a public reporting page. We issue API keys only to accounts with a verified email, so every key has an owner we can reach. Confirmed abuse is removed and reported.

The Free plan covers 100 uploads a month. Team is $4 a month: 2,000 uploads, 90-day expiry, 25 GB of storage. Scale is $12 a month: 10,000 uploads, year-long expiry, 100 GB. Prices include tax, everywhere in the world.

Start at veilfile.com. The whole thing is designed so the next PixelLeak does not have to happen.
