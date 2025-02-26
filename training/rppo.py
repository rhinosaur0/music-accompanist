import gym
from gym import spaces
import numpy as np
from typing import Optional
from data_processing import prepare_tensor
from math import log
import pretty_midi




class MusicAccompanistEnv(gym.Env):
    """
    
    Observations: A sliding window (3 x window_size) from the input data.
       - Row 0: Reference pitch
       - Row 1: Soloist pitch timing
       - Row 2: Reference pitch's metronomic timing
    """
    def __init__(self, data, window_size=10):
        super(MusicAccompanistEnv, self).__init__()
        self.data = data 
        self.n_notes = self.data.shape[1]
        self.window_size = window_size
        self.current_index = window_size  

        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(2 * (self.window_size) + 1,), dtype=np.float32
        )

        self.action_space = spaces.Box(
            low=0.3, high=3.0, shape=(1,), dtype=np.float32
        )

    def reset(self):
        # self.current_index = self.window_size
        # first_window = self.data[:, self.current_index - self.window_size:self.current_index].astype(np.float32)
        # first_window = first_window - first_window[:, 0:1] # normalize by setting the first time to 0
        # first_pred_note = self.data[1, self.current_index]
        # first_pred_tensor = np.full((1, self.window_size), first_pred_note)
        # first_obs = np.vstack((first_window, first_pred_tensor))
        # return first_obs
    
        self.current_index = self.window_size
        first_obs = self.data[:, self.current_index - self.window_size:self.current_index].astype(np.float32)
        first_obs = first_obs - first_obs[:, 0:1]  # Normalize by setting the first time to 0
        
        # Flatten the 2D window and append the additional float
        next_note_onset = self.data[1, self.current_index]  # Example: soloist's next note onset
        first_obs = np.concatenate([first_obs.flatten(), [next_note_onset]])  # Shape: (2 * (window_size - 1) + 1)
        
        print(f"First observation: {first_obs}")
        return first_obs

    def step(self, action):
        """
        Apply the speed adjustment factor to the reference timing,
        then compute the reward based on how close the predicted timing (ref_timing * action)
        is to the soloist's actual timing.
        """
        # Extract the current reference and soloist timing
        ref_timing = self.data[1, self.current_index] - self.data[1, self.current_index - 1]
        solo_timing = self.data[0, self.current_index] - self.data[0, self.current_index - 1]
        speed_factor = action[0]
        predicted_timing = ref_timing * speed_factor

        reward = self.reward_function(predicted_timing, solo_timing)
        
        self.current_index += 1
        done = (self.current_index >= self.n_notes)
        
        if not done:
            next_window = self.data[:, self.current_index - self.window_size:self.current_index].astype(np.float32)
            next_window = next_window - next_window[:, 0:1] # normalize by setting the first time to 0
            next_pred_note = self.data[1, self.current_index]
            # next_pred_tensor = np.full((1, self.window_size), next_pred_note)
            # obs = np.vstack((next_window, next_pred_tensor))
            obs = np.concatenate([next_window.flatten(), [next_pred_note]])
            # obs = next_window
            if self.current_index % 10 == 0:
                print(f"Current index: {self.current_index}, Reward: {reward}, Action: {action}")
        else:
            obs = np.zeros((2 * (self.window_size) + 1,), dtype=np.float32)
        info = {"predicted_timing": predicted_timing}
        return obs, reward, done, info

    def reward_function(self, predicted_timing, solo_timing):
        ratio_diff = log(predicted_timing / solo_timing) ** 2
        reward = -ratio_diff
        return reward

    def render(self, mode='human'):
        pass


from sb3_contrib import RecurrentPPO
from stable_baselines3.common.vec_env import DummyVecEnv

class RecurrentPPOAgent:
    def __init__(self, env, file_path: Optional[str] = None):
        self.file_path = file_path
        self.lstm_states = None
        # episode_starts must be an array-like value; we use shape (1,) here.
        self.episode_starts = np.ones((1,), dtype=bool)
        self.env = env
        self.model = None
        self._initialize()
        self.model.set_env(env)


    def _initialize(self) -> None:
        if self.file_path is None:
            self.model = RecurrentPPO("MlpLstmPolicy", self.env, verbose=0)
        else:
            self.model = RecurrentPPO.load(self.file_path)

    def reset(self) -> None:
        """Reset the agent's LSTM states and episode_start flag."""
        self.episode_starts = np.ones((1,), dtype=bool)
        self.lstm_states = None

    def predict(self, obs):
        """
        Predict an action given an observation, while maintaining LSTM states.
        The `episode_start` flag ensures that the recurrent network resets at the start of an episode.
        """
        action, self.lstm_states = self.model.predict(
            obs,
            state=self.lstm_states,
            episode_start=self.episode_starts,
            deterministic=True
        )
        self.episode_starts = np.zeros((1,), dtype=bool)
        return action

    def save(self, file_path: str) -> None:
        self.model.save(file_path)

    def learn(self, total_timesteps, log_interval: int = 1, verbose=0):
        """
        Set the environment, adjust verbosity, and begin training.
        """
        self.model.verbose = verbose
        self.model.learn(total_timesteps=total_timesteps, log_interval=log_interval)


def test_trained_agent(agent, env, n_episodes=1):
    """
    Run one or more episodes with the trained agent and record the predicted timings.
    Returns a list of predicted timing sequences (one per episode).
    """
    episodes_timings = []
    for episode in range(n_episodes):
        obs = env.reset()  # Reset environment
        agent.reset()      # Reset agent's LSTM states
        done = False
        total_reward = 0.0
        predicted_timings = []
        
        while not done:
            action = agent.predict(obs)
            obs, reward, done, info = env.step(action)
            # Save the predicted timing from this step
            predicted_timings.append(info[0].get("predicted_timing"))
            total_reward += reward[0]
            # print(f"Episode: {episode+1}, Action: {action}, Reward: {reward[0]:.4f}, Predicted Timing: {info[0].get('predicted_timing'):.4f}, Note: {info[0].get('note')}")
        
        episodes_timings.append(predicted_timings)
    return episodes_timings


def write_midi_from_timings(timings, notes, output_midi_file="output.mid", default_duration=0.3):
    """
    Given a sequence of predicted timing differences, compute cumulative onset times and write a MIDI file.
    Each note is assigned a constant pitch and fixed duration.
    """
    # Compute cumulative onset times: first note starts at time 0.
    note_onsets = [0]
    for a, t in enumerate(timings):
        if a < 20:
            print(f"Timing: {t}")
        note_onsets.append(note_onsets[-1] + t)
    
    # Create a PrettyMIDI object and a piano instrument.
    pm = pretty_midi.PrettyMIDI()
    piano_program = pretty_midi.instrument_name_to_program('Acoustic Grand Piano')
    piano = pretty_midi.Instrument(program=piano_program)
    
    print(len(note_onsets), len(notes))
    for onset, note in zip(note_onsets, notes[9:]):

        start_time = onset
        end_time = onset + default_duration  # fixed note duration
        note = pretty_midi.Note(velocity=100, pitch=int(note), start=start_time, end=end_time)
        piano.notes.append(note)
    
    pm.instruments.append(piano)
    pm.write(output_midi_file)
    print(f"MIDI file written to {output_midi_file}")

# ----------------------------
# Main Testing Script
# ----------------------------
if __name__ == "__main__":
    data = prepare_tensor("assets/real_chopin.mid", "assets/reference_chopin.mid")


    env = DummyVecEnv([lambda: MusicAccompanistEnv(data[1:, :], window_size=10)])
    agent = RecurrentPPOAgent(env)
    
    # Uncomment these lines to train/save the model if needed.
    # agent.learn(total_timesteps=10000, log_interval=10, verbose=1)
    # agent.save("recurrent_ppo_music_accompanist")
    
    # Load a pretrained model.
    agent.model = agent.model.load("models/0220_05")
    episodes_timings = test_trained_agent(agent, env, n_episodes=1)
    predicted_timings = episodes_timings[0]
 
    write_midi_from_timings(predicted_timings, data[0, :], output_midi_file="adjusted_output.mid", default_duration=0.3)

    
