# SupportIQ demo script

**Target runtime: 4:15** (leave 45 seconds under the five-minute limit). Use the guided story on Desk and a separate demo datapack seeded with the 100 fictional knowledge entries. Keep the four-page navigation visible in the recording.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:20 | Open Desk. Pause on the connection status, model, and five-step story. | “SupportIQ is a shared memory for support teams. It searches before answering, and new suggestions stay drafts until a person approves them.” |
| 0:20–0:55 | Select **Known problem**, send the prefilled fixture, and let the ledger stream. | “This case is already represented in the seeded knowledge. Triage chooses a support agent, Meko searches shared context, and the case records the result.” Read the outcome and agent shown on screen; do not narrate a different result. |
| 0:55–1:35 | Select **New problem**, send it, and show the answer and decision ledger. | “This issue is outside the seeded examples. The agent saves its cautious answer as a draft rather than treating it as policy.” If the returned stamp is not **New draft saved**, state the actual result and pause the walkthrough. |
| 1:35–2:05 | Select **Same issue, reworded**, submit the second fixture, and expand a ledger detail. | “A second agent can find the draft from the earlier case. It is still unverified, so the response asks for review.” Read the returned stamp. |
| 2:05–2:45 | Open Memory. Review who learned the draft and when, then explicitly select **Approve** and confirm. | “A person makes the promotion decision. SupportIQ rewrites the draft, updates it, and asks Meko to promote it into shared knowledge.” |
| 2:45–3:15 | Return to Desk using **Verified answer**, send the final fixture, and show its stamp. | “The same issue is asked again after approval. The case now shows what Meko actually returned.” Read the stamp, agent, and trace; if it is not verified, say so plainly. |
| 3:15–3:45 | Open Evidence. Show benchmark status or the stored benchmark metrics and the separate live-session section. | “These sections are intentionally separate. The benchmark page reads the saved run; live cases come from local history. No run means no benchmark numbers.” |
| 3:45–4:15 | Open How it works. Pan across the architecture and Meko tool table. | “Meko supplies shared search, memory, conversations, decision records, token tracking, and self-learning signals. The human approval step keeps a generated suggestion distinct from approved knowledge.” |

## Recording notes

- Stop before 4:30; the schedule leaves time for transitions but not for waiting on slow service calls.
- Do not send customer data. The fixtures are fictional and the app has no authentication.
- Do not click **Verify** during this walkthrough unless you intend to run that explicit self-learning replay.
- The approval is a one-way operation in Meko. Use only a newly created, isolated demo datapack.
- Show the actual status, latency, and token values in the app. Do not narrate a result that is absent from the screen.

## Motion cues

- Let the streamed decision steps arrive before describing the selected memory path; the running dot stops and completed steps receive a check.
- Pause briefly on the outcome stamp, then open Memory and approve a draft so viewers can follow the draft moving into approved knowledge.
- The Agents page highlights the assigned agent and shows handled-versus-reused counts without replaying on an unchanged refresh.
- Two short headless-browser recordings are generated in [`motion-recordings/`](./motion-recordings/) by the motion check.
