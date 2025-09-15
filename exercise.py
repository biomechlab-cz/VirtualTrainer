import models.mautner.model as Mautner
from processing import process_mautner


# TODO: Implement logic to handle different exercises
class Exercise:
    def __init__(self, name=None):
        self.name = name

        # Models
        self.wide_squat_model = Mautner.ModelInterface()

    def describe(self):
        return {}

    def predict_wide_squat_phase(self, data):
        # Process input data according to model requirements
        processed_data = process_mautner(data)

        # Make prediction
        return self.wide_squat_model.predict(processed_data)
