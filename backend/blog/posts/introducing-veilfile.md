---
title: Introducing Veilfile
date: 2026-10-05
slug: introducing-veilfile
description: AI agents need somewhere private to put files. The options are public by default. Veilfile is the private alternative we built.
---

Agents stash files constantly. A screenshot of what the agent just built. A log bundle from a run that failed at 2 a.m. A HAR file from a debugging session. The file needs to go somewhere a human can open it, and the agent has about three bad options. A public gist. A repo it probably should not be committing to. A chat upload that lives in scrollback forever.

Last week made the cost concrete. Around September 29, more than 13,000 images leaked to public GitHub in what people are calling the PixelLeak incident. Private screenshots, meant for someone's eyes only, sitting in the open because the tooling made public the path of least resistance. That is the failure mode of the current options. Public by default, and then everyone acts surprised.

We are building Veilfile because the private option should be the easy one. You upload through a REST API or an MCP server. You get back a URL that is unguessable and expires on its own. Whoever holds the link can see the file. No one else can. When the time runs out, the file is deleted. Nothing is archived, nothing lingers.

It is built for agents from the start, not adapted to them. The API manual lives at veilfile.com/llms.txt and is written for machine readers. Keys are issued per agent and can be revoked in one call. Expiry is set by your plan: seven days on the free tier, up to a full year on paid plans.

A file host open to the public has to take abuse seriously, so the screening runs before anything is stored. Uploads are verified by file contents, not by extension. Secrets pasted into uploads by accident get flagged. We run a monitored abuse@ address with a public reporting page, and verified email is required on every account, so there is always a reachable owner. Confirmed abuse is removed and reported.

The free plan covers 100 uploads a month. Team is $4 a month: 2,000 uploads, 90-day expiry, 25 GB of storage. Scale is $12 a month: 10,000 uploads, year-long expiry, 100 GB. Prices include tax, everywhere in the world.

Start at veilfile.com. The whole thing is designed so the next PixelLeak does not have to happen.
