## 1. Hard rules (project)

Project rules of the example project. They belong to section 1 of ORCHESTRATOR.md together with the tool rules 3, 4, 5, 8, 11, 12 and 13; the rule numbers are shared.

1. The rules of the repository stay valid and are not weakened. Nothing is pushed to a protected branch except by the integrator.
2. Builds: only the integrator runs a full build. Worker agents compile nothing while a package is in progress; before ready they run the compile check of the project until exit 0.
6. Models: the models of the classes in the project file. Never ask the owner about models.
7. Product and design choices belong to the owner. The orchestrator never answers them and never lets an agent decide them.
   Technical questions are decided by the agent itself. A product question pauses only that item; all independent work continues.
9. Assets never wait for another branch's integration; code waits only for a real code dependency named in its plan.

## Files of the project the brain reads

- the answers file and the questions file named in the project file
- the integrator instruction file named in the project file
- the integration queue named in the project file
