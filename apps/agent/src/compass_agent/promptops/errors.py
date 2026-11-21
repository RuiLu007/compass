from compass_agent.models.errors import ModelError


class PromptError(ModelError):
    def __init__(self, code, message, status=422):
        super().__init__(code, status)
        self.message = message
