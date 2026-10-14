# Dependency boundary

The offline demo uses the Python standard library and included Causyl code.
The supported test environment uses `requirements-test.txt`; exact installed
metadata and license evidence are recorded in the private preparation evidence.

Optional modules import PyYAML, requests, pathspec, patch-ng, rich, ruamel.yaml,
NumPy, pandas, scikit-learn, matplotlib, tiktoken, LangGraph, langchain-ollama,
OpenAI and xAI SDKs. Their imports are not evidence that a provider or tool is
installed or configured. This preview does not install or validate that entire
optional graph. Dependency packages, model weights and external tools are not
vendored or redistributed here.

Android sources declare their dependencies in Gradle. The source preview omits
the wrapper JAR and binary artwork; a complete Android dependency/license graph
and build must be validated before distributing an APK.

The old environment-wide CUDA/PyTorch freeze is excluded. Python wheel installation
is not supported yet because some modules require checkout-relative contracts and
knowledge assets. Use a source checkout for the demonstrated workflow.
