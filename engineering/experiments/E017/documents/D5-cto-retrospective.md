# Engineering Team Retrospective — Personal Draft

**Author:** Sarah Winters, CTO
**Date:** October 1, 2026
**Status:** DRAFT — NOT FOR DISTRIBUTION
**Note:** I'm writing this to organize my own thoughts before our strategy offsite. This is not a formal document.

---

## What's been hard

I need to be honest with myself about where I am. We're 14 months post-Series A and I'm not sure we're building the right things.

When I joined as the first engineer three years ago, the vision was to build a platform — APIs, data connectors, a query engine that other products could plug into. We talked about being the "analytics infrastructure layer" that powered insights across tools. That's what got me excited.

What we've actually built over the past year is a series of user-facing features. The AI insights module was the flagship initiative of the current product roadmap — it consumed two quarters of engineering time and roughly **$400,000** in salary and infrastructure cost. I'm proud of the technical work the team did on it. But the business outcome has been... mixed. Adoption numbers aren't where I'd hoped they'd be — the AI-powered insights module has only 23% feature adoption among our user base. And I suspect we're about to get asked to build another feature on top of it instead of addressing the underlying platform gaps.

The thing that bothers me most is that we keep adding surface area without strengthening the foundation. Our APIs are undocumented. We don't have proper multi-tenancy. The data pipeline breaks at volume. The roadmap keeps prioritizing visible features over invisible reliability. These are the kinds of problems I want to solve — the hard infrastructure problems — but the current roadmap doesn't make space for them.

---

## The team

We have **14 engineers**, which is a solid team. But I look at the skills distribution and I see a gap: we don't have anyone with enterprise security or compliance experience. No one on the team has ever built SSO integration, an audit log system, or gone through a SOC 2 certification process.

These aren't hard problems from a pure engineering perspective — they're well-understood patterns. But doing them for the first time with a team that has never done them introduces risk. We'd need either to hire someone who has done it before, or accept that our first attempt will involve learning on the job.

My frustration is that I *want* to build these things. SSO, APIs, audit trails, multi-tenancy — this is platform engineering. This is the work I came here to do. But the current roadmap doesn't prioritize any of it.

---

## What I haven't said out loud

I've talked to the CEO about this privately. I told her that if the next 12 months look like the last 12 months — feature after feature, no platform investment — I'm not sure this is the right place for me anymore.

I don't want to leave. I believe in what this company could be. But I'm a platform engineer, not a feature factory. If the strategy doesn't create space for the kind of work I'm best at, then staying wouldn't be good for me or for the company.

We have the strategy offsite coming up. That's where I need to understand whether the direction aligns with what I can contribute, or whether we need to have a different conversation.

---

*This is a personal draft. If you're reading this and you're not Sarah, this document was shared in error — please delete it and let me know.*
