#!/usr/bin/env python

from __future__ import division, print_function
import csv
import os

import logging
from pathlib import Path

import numpy as np
from direct.task import Task
from panda3d.core import CardMaker, ClockObject

from base_tunnel import BaseTunnel
from misc import default_main
import time


class Flipper:

    def __init__(self, probas, max_noflip_trials):
        assert np.isclose(np.sum(probas), 1)
        self.probas = np.array(probas) / np.sum(probas)
        n_stim = len(probas)

        # define the number of transitions without flip, i.e same texture
        max_noflip_trials = np.atleast_1d(max_noflip_trials)
        if len(max_noflip_trials) != 1 and len(max_noflip_trials) != n_stim:
            raise ValueError(
                "'max_noflip_trials' should have 1 or {} elements."
                .format(n_stim)
            )
        self.max_noflip_trials = np.empty(n_stim)
        self.max_noflip_trials[...] = max_noflip_trials

        self.stim_trials = np.zeros(n_stim)
        self.noflip_trials = 0
        self.stim_id = None

    def __str__(self):
        return "stim_id: {}, noflip_trials: {}, stim_trials: {}".format(
            self.stim_id, self.noflip_trials, self.stim_trials
        )

    @property
    def n_stim(self):
        return len(self.probas)

    def next_stim_id(self):
        # randomly generate the next stimulus ID
        p = self.probas.copy()

        # avoid more than 'max_noflip_trials' trials with the same stimulus
        nnz_p = len(p.nonzero()[0])

        if self.stim_id is None:  # very first trial
            max_noflip = np.inf
        else:
            max_noflip = self.max_noflip_trials[self.stim_id]

        if nnz_p > 1 and self.noflip_trials >= max_noflip:
            p[self.stim_id] = 0
            p /= p.sum()

        sample = np.random.multinomial(1, p)
        next_stim_id = sample.nonzero()[0][0]

        # update counter of noflip trials
        if next_stim_id == self.stim_id:
            self.noflip_trials += 1
        else:
            self.noflip_trials = 0

        self.stim_id = next_stim_id
        self.stim_trials[next_stim_id] += 1

        return self.stim_id


class BlockFlipper:

    def __init__(self, probas, n_block):
        assert np.isclose(np.sum(probas), 1)
        self.probas = np.array(probas) / np.sum(probas)
        self.n_block = n_block

        self.stim_id = None
        self.cnt = 0

        counts = [p * n_block for p in probas]
        if any(not np.isclose(cr, int(cr)) for cr in counts):
            raise ValueError('Block size does not allow to draw whole number '
                             'samples for all stimulus types.')

        block = [np.full(int(c), i) for i, c in enumerate(counts)]
        self.block = np.concatenate(block)
        np.random.shuffle(self.block)

    def __str__(self):
        return "stim_id: {} ({} / {})".format(
            self.stim_id, self.cnt, self.n_block
        )

    @property
    def n_stim(self):
        return len(self.probas)

    def next_stim_id(self):
        self.stim_id = self.block[self.cnt]

        self.cnt += 1
        if self.cnt >= self.n_block:
            self.cnt = 0
            np.random.shuffle(self.block)

        return self.stim_id


class Follower:

    def __init__(self, flipper):
        self.flipper = flipper

    def __str__(self):
        return str(self.flipper) + ' (linked)'

    @property
    def n_stim(self):
        return self.flipper.n_stim

    def next_stim_id(self):
        return self.flipper.stim_id


class FlipSection:

    def __init__(self, section, stimulus_textures, stimulus_onset, triggers,
                 flipper):
        assert len(stimulus_textures) == len(triggers)
        assert len(stimulus_textures) == flipper.n_stim

        self.section = section
        self.onset = section.getPos()[1] - stimulus_onset
        self.offset = section.getPos()[1] + stimulus_onset

        self.neutral_texture = section.getTexture()
        self.stimulus_textures = stimulus_textures

        self.flipper = flipper
        self.triggers = triggers

        self.stim_id = None
        self.triggered = False
        self.reset()

    @property
    def n_stim(self):
        return len(self.stimulus_textures)

    def reset(self):
        self.section.setTexture(self.neutral_texture, 1)  # reset texture
        self.stim_id = self.flipper.next_stim_id()
        self.triggered = False

    def update(self, tunnel):
        # do nothing if outside of triggering zone
        if tunnel.position < self.onset or tunnel.position > self.offset:
            return False

        # do nothing if already triggered
        if self.triggered:
            return True

        self.triggered = True

        # change the texture for the current stimulus
        self.section.setTexture(self.stimulus_textures[self.stim_id], 1)

        # start a trigger, if any for the current stimulus
        for trigger in self.triggers[self.stim_id]:
            tunnel.taskMgr.add(
                trigger, 'start_trigger_task', sort=-20,
                extraArgs=[tunnel.position], appendTask=True
            )

        return True


class FlipTunnel:

    def __init__(self, tunnel, card, flip_sections, inputs, outputs,
                 sleep_time, test_mode, options):
        """_summary_

        Args:
            tunnel (_type_): _description_
            card (_type_): _description_
            flip_sections (_type_): _description_
            inputs (_type_): _description_
            outputs (tuple(key: AnalogOutput)): handles NIDAQ control, in this case is a reward valve.
                                    .write_float method uses daq.Task.WriteAnalogScalarF64
            sleep_time (_type_): _description_
            test_mode (_type_): _description_
        """

        logging.basicConfig()
        self.logger = logging.getLogger(__name__)
        self.tunnel = tunnel
        self.current_flip_sections = []
        self.flip_sections = flip_sections
        self.card = card
        self.sleep_time = sleep_time
        self.inputs = inputs
        self.outputs = outputs

        try:
            self.lock_corridor_reward = options['flip_tunnel']['lock_corridor_reward']
        except:
            self.lock_corridor_reward = True

        self.globalClock = ClockObject.getGlobalClock()

        self.total_forward_run_distance = 0

        # This will make the later analysis so much easier
        self.sample_i = 0

        # self.goals = [[0, 9], [36, 45], [54, 63]]
        try:
            self.goals = options['flip_tunnel']['goals']
        except:
            self.goals = [[0, 9]]
        self.goalNums = len(self.goals)
        try:
            self.currentGoal = options['flip_tunnel']['initial_goal']
        except:
            self.currentGoal = 0
        self.currentGoalIdx = 0

        try:
            manual_reward_with_space = options['flip_tunnel']['manual_reward_with_space']
        except:
            manual_reward_with_space = False

        self.ruleName = options['sequence_task']['rulename']

        self.isChallenged = False
        self.wasChallenged = False
        self.wasRewarded = False
        self.wasManuallyRewarded = False
        self.wasAssistRewarded = False

        self.assist_sound_playing = False
        self.main_sound_playing = False

        self.odour_prepped = False
        self.olf_done = False
        self.flush_done = True

        self.current_landmark = 0

        try:
            self.reward_prob = options['flip_tunnel']['reward_prob']
        except:
            self.reward_prob = 1
        try:
            self.assist_sound_volume = options['flip_tunnel']['assist_sound_volume']
        except:
            self.assist_sound_volume = 0.3
        try:
            self.main_sound_volume = options['flip_tunnel']['main_sound_volume']
        except:
            self.main_sound_volume = 0.3
        try:
            self.reward_tone_length = options['flip_tunnel']['reward_tone_length']
        except:
            self.reward_tone_length = 1
        try:
            self.assist_reward_prob_decay = options['flip_tunnel']['assist_reward_prob_decay']
        except:
            self.assist_reward_prob_decay = 0.9

        try:
            self.punishBySpeedGain = options['flip_tunnel']['punishBySpeedGain']
        except:
            self.punishBySpeedGain = False
        try:
            self.no_reward_cue = options['flip_tunnel']['no_reward_cue']
        except:
            self.no_reward_cue = False

        self.isLogging = False

        self.triggeResetPosition = options['flip_tunnel']['length']
        try:
            self.triggeResetPositionStart = options['flip_tunnel']['margin_start']
            # self.reset_camera(self.triggeResetPositionStart)
        except:
            self.triggeResetPositionStart = 0

        self.flip_tunnel_options = options['flip_tunnel']
        self.flip_tunnel_options['corridor_len'] = options['flip_tunnel']['length'] - \
            options['flip_tunnel']['margin_start']
        print('corridor length is ', self.flip_tunnel_options['corridor_len'])

        self.create_nidaq_controller(options)

        try:
            foldername = options['logger']['foldername']
            self.setup_logfile(foldername)
            self.isLogging = True
        except:
            self.isLogging = False

        if test_mode:
            self.tunnel.accept("space", self.spacePressed)
        if manual_reward_with_space:
            self.tunnel.accept("space", self.manualReward)

        # Add a task to check for the space bar being pressed
        self.tunnel.taskMgr.add(self.checkIfReward, "CheckIfRewardTask")

        # self.tunnel.taskMgr.add(self.checkIfPunished, "checkIfPunishedTask")

        for i, section in enumerate(self.flip_sections):
            self.logger.info('section_id %d, new stim %d', i, section.stim_id)

        # add a task to the tunnel to update walls according to the position
        self.tunnel.taskMgr.add(
            self.update_tunnel_task, 'update_tunnel_task', sort=5
        )

        # add an input task, with higher priority (executed before moving)
        self.tunnel.taskMgr.add(
            self.update_inputs_task, 'update_inputs_task', sort=-10
        )

        # add an output with lower priority (executed after updating tunnel)
        self.tunnel.taskMgr.add(
            self.update_outputs_task, 'update_outputs_task', sort=10
        )

        if self.isNIDaq:
            self.tunnel.taskMgr.add(
                self.update_piezo_task, 'update_piezo_task'
            )

        if self.isLogging:
            self.tunnel.taskMgr.add(
                self.position_logging_task, 'position_logging_task'
            )
            # self.tunnel.taskMgr.add(
            #     self.event_logging_task, 'event_logging_task'
            # )

        if 'assisted_goals' in options['flip_tunnel']:
            print('Using assisted goals....')
            self.assisted_goals = options['flip_tunnel']['assisted_goals']
            self.tunnel.taskMgr.add(
                self.assist_reward_task, 'assist_reward_task'
            )
        if 'assist_tone_goals' in options['flip_tunnel']:
            print('using assisted tones')
            self.assist_tone_goals = options['flip_tunnel']['assist_tone_goals']
            self.tunnel.taskMgr.add(
                self.assist_tone_task, 'assist_tone_task'
            )

        if 'landmarks' in options['flip_tunnel']:
            print('using landmarks')
            self.landmarks = options['flip_tunnel']['landmarks']
            self.LMNums = len(self.landmarks)
            self.currentLMIdx = 0
            self.tunnel.taskMgr.add(
                self.olfact_task, 'olfact_task'
            )

        self.reward_length = {
            'manual': 0.1,
            'correct': 0.3,
            'assist': 0.2,
            'wrong': 0.15}

        try:
            self.reward_length.update(options['flip_tunnel']['reward_length'])
        except:
            pass

        self.odour_diffs = {
            'final-five': 0,
            'landmark': 2,
            'flush':1,
            'odour_overlap': 0.05}
        
        try:
            self.odour_diffs.update(options['flip_tunnel']['odour_diffs'])
        except:
            pass
        

        self.sound_mode = 'correct'

        if self.reward_length['correct'] != self.reward_length['wrong']:
            self.punishByRewardDecrease = True
        else:
            self.punishByRewardDecrease = False


        if 'sound_dir' in options['flip_tunnel']:
            self.use_sound = True
            sound_dir = options['flip_tunnel']['sound_dir']
            self.sounds = {}
            for key in options['flip_tunnel']['sounds'].keys():
                self.sounds[key] = self.tunnel.loader.loadSfx(
                    os.path.join(sound_dir, options['flip_tunnel']['sounds'][key]))
            # print('correct sound is loaded
            # print(self.sounds['correct'])
        else:
            self.use_sound = False
        if self.no_reward_cue:
            self.use_sound = False

    def setup_logfile(self, foldername):
        # Check if the directory exists, if not, create it
        if not os.path.exists(foldername):
            os.makedirs(foldername)

        # Setup position log file
        position_filename = os.path.join(foldername, 'position_log.csv')
        position_file_exists = Path(position_filename).is_file()
        position_file = open(position_filename, 'a', newline='')
        self.position_writer = csv.writer(position_file)
        if not position_file_exists:
            self.position_writer.writerow(
                ["Index", "Time", "Position", "TotalRunDistance", "Event"])

        # # Setup event log file
        # event_filename = os.path.join(foldername, 'event_log.csv')
        # event_file_exists = Path(event_filename).is_file()
        # event_file = open(event_filename, 'a', newline='')
        # self.event_writer = csv.writer(event_file)
        # if not event_file_exists:
        #     self.event_writer.writerow(["Time", "Event"])

    def spacePressed(self):
        print(self.tunnel.position)
        self.isChallenged = True

    def manualReward(self):
        # print('playing sound...')
        # self.correct_sound.play()
        # time.sleep(1)
        # self.correct_sound.stop()
        # print('sound stopped')
        self.wasManuallyRewarded = True
        self.triggerReward(mode='manual')

    # def checkIfPunished(self, task):
    #     # print('check if punished')
    #     if self.isChallenged and self.isAirpuff:
    #         print('checking is air puff is needed')
    #         if self.checkWithinPreviousOrCurrentGoal()!=True:
    #             print('triggering airpuff...')
    #             self.triggerAirpuff()

    #     if self.isChallenged and self.punishBySpeedGain:
    #         if self.checkWithinPreviousOrCurrentGoal()!=True:
    #             self.triggerGainDecrease()

    def checkIfPunished(self):
        # print('check if punished')
        if self.isAirpuff:
            # print('checking is air puff is needed')
            if self.checkWithinPreviousOrCurrentGoal() != True:
                print('triggering airpuff...')
                self.triggerAirpuff()

        if self.punishBySpeedGain:
            if self.checkWithinPreviousOrCurrentGoal() != True:
                self.triggerGainDecrease()

        if self.punishByRewardDecrease:
            if self.checkWithinPreviousOrCurrentGoal() != True:
                self.sound_mode = 'wrong'

    def checkIfReward(self, task):
        if self.isChallenged:
            self.checkIfPunished()
            print("licked, current pos: ", self.tunnel.position,
                  " current goal ", self.goals[self.currentGoalIdx])
            self.isChallenged = False
            self.wasChallenged = True
            if self.checkWithinGoal():
                #implement a coin flip to determine if the reward is given
                if np.random.rand() <= self.reward_prob:
                    print('correct! Getting reward...')
                    self.wasRewarded = True
                    # either correct or wrong
                    self.triggerReward(mode=self.sound_mode)
                    self.handleNextGoal()
                else:
                    print('unlucky! Getting no reward...')
                    self.wasRewarded = False
                    self.handleNextGoal()
                

        if self.ruleName in ['run-auto', 'protocol1_lv1']:
            pos = self.tunnel.position
            # position will be something like 99.0011 before being 9.0011, and it will cause bugs.
            # to prevent this, always subtract 90 if it is larger than 90
            if pos > self.flip_tunnel_options['length']:
                pos = pos - 90
            if pos + self.total_forward_run_distance > self.currentGoal:
                # print(self.tunnel.position)
                # print(self.total_forward_run_distance)
                # print(self.currentGoal)
                self.wasRewarded = True
                self.triggerReward(mode='correct')
                self.handleNextGoal()

        return Task.cont

    def assist_reward_task(self, task):
        goals = self.assisted_goals[self.currentGoalIdx]
        position = self.tunnel.position
        if position > goals[0] and position < goals[1]:
            if self.ruleName in ['protocol5_lv3']:
                if np.random.rand() <= self.reward_prob:
                    print('Getting reward with assist')
                    self.wasAssistRewarded = True
                    self.triggerReward(mode='assist')
                    self.handleNextGoal()

            else:
                print('Getting reward with assist')
                self.wasAssistRewarded = True
                self.triggerReward(mode='assist')
                self.handleNextGoal()

        return Task.cont

    def assist_tone_task(self, task):
        goals = self.assist_tone_goals[self.currentGoalIdx]
        position = self.tunnel.position
        if position > goals[0] and position < goals[1] and not self.assist_sound_playing:
            print('playing assist sound')
            self.assist_sound_playing = True
            mode = self.currentGoalIdx
            sound = self.sounds[mode]
            sound.setLoop(True)
            sound.setVolume(self.assist_sound_volume)
            sound.play()

        elif (position < goals[0] or position > goals[1]) and self.assist_sound_playing:
            print('stopping assist sound')
            self.stop_assist_sound()

        return Task.cont

    def main_tone_task(self, task):
        """
        using auditory cue as a main modality
        """

        position = self.tunnel.position
        landmarks = self.landmarks
        found_index = -1  # Initialize found_index with -1
        for index, landmark in enumerate(landmarks):
            if position > landmark[0] and position < landmark[1]:
                found_index = index
                break
        # print(found_index)
        if found_index != -1 and not self.main_sound_playing:
            self.main_sound_playing = True
            sound = self.sounds[found_index]
            sound.setLoop(True)
            sound.setVolume(self.main_sound_volume)
            sound.play()
            self.current_landmark = found_index

        elif found_index == -1 and self.main_sound_playing:
            self.stop_main_sound()

        return Task.cont
    
    def olfact_task(self, task):
        position = self.tunnel.position
        landmarks = self.landmarks[self.currentLMIdx]
        prep_index = -1
        
        if not self.odour_prepped and self.flush_done:
            self.prepOdourStim(odour=self.currentLMIdx)
            self.odour_prepped = True

        if position > landmarks[0] and position < landmarks[1]:
            prep_index = self.currentLMIdx

        if prep_index != -1 and not self.olf_done and self.odour_prepped:
            self.olf_done = True
            self.triggerOdourOn(odour=prep_index)
            self.flush_odour(odour=self.currentLMIdx)
            self.handleNextLM()
            prep_index = -1
        
        return Task.cont
        


    def triggerReward(self, mode='correct'):
        if self.assist_sound_playing:
            self.stop_assist_sound()
        try:
            length = self.reward_length[mode]
        except:
            raise ValueError('mode should be one of {}'.format(
                self.reward_length.keys()))

        if self.reward_tone_length == 0:
            tone_length = length
        else:
            tone_length = self.reward_tone_length

        print('stopping after {} sec'.format(length))

        if self.ruleName in ['audio-guided-sequence', 'protocol5_lv3']:
            print('current goal is {}'.format(self.currentGoalIdx))
            mode = self.currentGoalIdx
        if self.use_sound:
            sound = self.sounds[mode]

        print(length, tone_length, mode, self.use_sound)

        if self.isNIDaq:
            self.valveController.start()
            if self.use_sound:
                sound.setVolume(1)
                sound.play()
            if self.lock_corridor_reward:
                time.sleep(length)
                self.valveController.stop()
                if self.use_sound:
                    sound.stop()
            else:
                self.tunnel.taskMgr.doMethodLater(
                    length, self.stop_valve_task, 'stop_valve_task'
                )
                if self.use_sound:
                    self.sound_mode = mode
                    self.tunnel.taskMgr.doMethodLater(
                        tone_length, self.stop_sound_task, 'stop_sound_task'
                    )
        else:
            if self.use_sound:
                sound.play()
                if self.lock_corridor_reward:
                    time.sleep(length)
                    if self.use_sound:
                        sound.stop()
                else:
                    self.sound_mode = mode
                    self.tunnel.taskMgr.doMethodLater(
                        tone_length, self.stop_sound_task, 'stop_sound_task'
                    )
            print(self.tunnel.position)
            # time.sleep(length)
            print('reward is triggered')
    
    def prepOdourStim(self,odour):
        if self.isNIDaq:
            
            if odour in [0,2,4,6,8]:
                self.threevalve2controller.stop()
                print('closing 3way valve 2')
            else:
                self.threevalvecontroller.stop()
                print('closing 3way valve 1')
            self.odourcontroller6.stop()
            self.odourcontroller12.stop()

            # if odour == 0:
            #     self.finalvalve2controller.stop()
            #     self.finalvalve1controller.start()
            #     self.odourcontroller1.start()
            #     print('opened odour valve 1')
            # elif odour == 1:
            #     self.finalvalve1controller.stop()
            #     self.finalvalve2controller.start()
            #     self.odourcontroller2.start()
            #     print('opened odour valve 2')
            # elif odour == 2:
            #     self.finalvalve2controller.stop()
            #     self.finalvalve1controller.start()
            #     self.odourcontroller3.start()
            #     print('opened odour valve 3')
            # elif odour == 3:
            #     self.finalvalve1controller.stop()
            #     self.finalvalve2controller.start()
            #     self.odourcontroller4.start()
            #     print('opened odour valve 4')
            # elif odour == 4:
            #     self.finalvalve2controller.stop()
            #     self.finalvalve1controller.start()
            #     self.odourcontroller5.start()
            #     print('opened odour valve 5')
            # elif odour == 5:
            #     self.finalvalve1controller.stop()
            #     self.finalvalve2controller.start()
            #     self.odourcontroller7.start()
            #     print('opened odour valve 7')
            # elif odour == 6:
            #     self.finalvalve2controller.stop()
            #     self.finalvalve1controller.start()
            #     self.odourcontroller8.start()
            #     print('opened odour valve 8')
            # elif odour == 7:
            #     self.finalvalve1controller.stop()
            #     self.finalvalve2controller.start()
            #     self.odourcontroller9.start()
            #     print('opened odour valve 9')
            # elif odour == 8:
            #     self.finalvalve2controller.stop()
            #     self.finalvalve1controller.start()
            #     self.odourcontroller10.start()
            #     print('opened odour valve 10')
            # elif odour == 9:
            #     self.finalvalve1controller.stop()
            #     self.finalvalve2controller.start()
            #     self.odourcontroller11.start()
            #     print('opened odour valve 11')
            # else:
            #     print('no corresponding valve found, no odour')

            self.flush_done = False
            if odour in [0,2,4,6,8]:
                self.fivevalvecontroller.start()
                print('opening 5way valve to position A')
            else:
                self.fivevalvecontroller.stop()
                print('opening 5way valve to position B')
        else:
            print(self.tunnel.position)
            print('prepping odour {}'.format(odour))


    def triggerOdourOn(self,odour):

        if self.isNIDaq:
            
            if odour in [0,2,4,6,8]:
                self.threevalvecontroller.start()
            else:
                self.threevalve2controller.start()
            print(self.tunnel.position)
            print('odour {} is triggered'.format(odour))
            print('closing after {}'.format(self.odour_diffs['odour_overlap']))
            self.tunnel.taskMgr.doMethodLater(
                        self.odour_diffs['odour_overlap'], self.stop_odour_valve, 'stop_odour_valve'
                    ) 
            if odour in [0,2,4,6,8]:
                print('opening mineral oil modd 1')
                self.odourcontroller6.start() #mineral oil modd 1
            else:
                print('opening mineral oil modd 2')
                self.odourcontroller12.start() #mineral oil modd 2
                
        else:
            print(self.tunnel.position)
            print('triggering odour stimulation for {}'.format(self.odour_diffs['odour_overlap']))        

    
    def stop_three_valve(self,odour):
        odour = self.currentLMIdx
        
        if odour in [0,2,4,6,8]:
            self.threevalvecontroller.stop()
            print('closing 3way valve 1')
        else:
            self.threevalve2controller.stop()
            print('closing 3way valve 2')
        self.flush_done = True
        self.olf_done = False
        self.odour_prepped = False

    
    def stop_odour_valve(self,odour):
        print('closing odour valve')
        
        self.odourcontroller1.stop()
        self.odourcontroller2.stop()
        self.odourcontroller3.stop()
        self.odourcontroller4.stop()
        self.odourcontroller5.stop()
        self.odourcontroller7.stop()
        self.odourcontroller8.stop()
        self.odourcontroller9.stop()
        self.odourcontroller10.stop()
        self.odourcontroller11.stop()
    
    def load_next_odour(self,odour):
        odour = self.currentLMIdx + 1
        if odour > 9:
            odour = 0
    
        if odour == 0:
            self.finalvalve2controller.stop()
            self.finalvalve1controller.start()
            self.odourcontroller1.start()
            print('opened odour valve 1')
        elif odour == 1:
            self.finalvalve1controller.stop()
            self.finalvalve2controller.start()
            self.odourcontroller2.start()
            print('opened odour valve 2')
        elif odour == 2:
            self.finalvalve2controller.stop()
            self.finalvalve1controller.start()
            self.odourcontroller3.start()
            print('opened odour valve 3')
        elif odour == 3:
            self.finalvalve1controller.stop()
            self.finalvalve2controller.start()
            self.odourcontroller4.start()
            print('opened odour valve 4')
        elif odour == 4:
            self.finalvalve2controller.stop()
            self.finalvalve1controller.start()
            self.odourcontroller5.start()
            print('opened odour valve 5')
        elif odour == 5:
            self.finalvalve1controller.stop()
            self.finalvalve2controller.start()
            self.odourcontroller7.start()
            print('opened odour valve 7')
        elif odour == 6:
            self.finalvalve2controller.stop()
            self.finalvalve1controller.start()
            self.odourcontroller8.start()
            print('opened odour valve 8')
        elif odour == 7:
            self.finalvalve1controller.stop()
            self.finalvalve2controller.start()
            self.odourcontroller9.start()
            print('opened odour valve 9')
        elif odour == 8:
            self.finalvalve2controller.stop()
            self.finalvalve1controller.start()
            self.odourcontroller10.start()
            print('opened odour valve 10')
        elif odour == 9:
            self.finalvalve1controller.stop()
            self.finalvalve2controller.start()
            self.odourcontroller11.start()
            print('opened odour valve 11')
        else:
            print('no corresponding valve found, no odour')
        

    def flush_odour(self,odour):
        print('presenting odour for {}'.format(self.odour_diffs['landmark']))
        self.tunnel.taskMgr.doMethodLater(
                        self.odour_diffs['landmark'], self.load_next_odour, 'load_next_odour'
                    )
        print('flushing odour for another {}'.format(self.odour_diffs['flush']))
        self.tunnel.taskMgr.doMethodLater(
                        self.odour_diffs['flush'], self.stop_three_valve, 'stop_three_valve'
                    )


    def triggerAirpuff(self):
        if self.isNIDaq:
            self.airpuffController.start()
            print('air puffed')
            if self.lock_corridor_reward:
                time.sleep(0.1)
                self.airpuffController.stop()
            else:
                self.tunnel.taskMgr.doMethodLater(
                    0.1, self.stop_airpuff_task, 'stop_airpuff_task'
                )
        else:
            time.sleep(0.1)
            print('air puffed')

    def triggerGainDecrease(self, scale=0.05, min_speed_gain=0.1, delay=3):
        speed_gain_before = self.speed_gain
        self.speed_gain = max(min_speed_gain, self.speed_gain - scale)

        self.tunnel.taskMgr.doMethodLater(
            delay, self.increaseSpeeGain, 'increaseSpeeGain'
        )
        print('speed gain decreased from {} to {}'.format(
            speed_gain_before, self.speed_gain))

    def increaseSpeeGain(self, task, scale):
        self.speed_gain = self.speed_gain + scale

    # def checkAssistGoal(self):
    #     goals = self.assisted_goals[self.currentGoalIdx]
    #     position = self.tunnel.position
    #     print('checkAssistGoal {} > {} and {}'.format(position, goals[0], goals[1]))
    #     if position > goals[0] and position < goals[1]:
    #         print('true')
    #         return True
    #     return False

    def checkWithinGoal(self):
        if self.ruleName in ['sequence', 'audio-guided-sequence', 'protocol5_lv3', 'olfactory_support']:
            goals = self.goals[self.currentGoalIdx]
            position = self.tunnel.position
            if position > goals[0] and position < goals[1]:
                return True
            return False
        elif self.ruleName in ['all', 'olfactory_shaping1']:
            position = self.tunnel.position
            for goals in self.goals:
                if position > goals[0] and position < goals[1]:
                    return True
            return False
        elif self.ruleName in ['protocol1_lv2']:
            position = self.tunnel.position
            if self.tunnel.position + self.total_forward_run_distance > self.currentGoal:
                for goals in self.goals:
                    if position > goals[0] and position < goals[1]:

                        return True
            return False
        elif self.ruleName in ['run-lick']:
            print(self.tunnel.position +
                  self.total_forward_run_distance,  self.currentGoal)
            if self.tunnel.position + self.total_forward_run_distance > self.currentGoal:
                return True
            return False
        
    def checkWithinLandmark(self):
        if self.ruleName in ['sequence', 'audio-guided-sequence', 'protocol5_lv3']:
            landmarks = self.landmarks[self.currentLMIdx]
            position = self.tunnel.position
            if position > landmarks[0] and position < landmarks[1]:
                return True
            return False
        elif self.ruleName in ['olfactory_support']:
            position = self.tunnel.position
            landmarks = self.landmarks[self.currentLMIdx]
            if position > landmarks[0] and position < landmarks[1]:
                print('landmark {} detected'.format(self.currentLMIdx))
                return True
            return False
        elif self.ruleName in ['protocol1_lv2']:
            position = self.tunnel.position
            if self.tunnel.position + self.total_forward_run_distance > self.current_landmark:
                for landmarks in self.landmarks:
                    if position > landmarks[0] and position < landmarks[1]:

                        return True
            return False
        elif self.ruleName in ['run-lick']:
            print(self.tunnel.position +
                  self.total_forward_run_distance,  self.current_landmark)
            if self.tunnel.position + self.total_forward_run_distance > self.current_landmark:
                return True
            return False
        

    def checkWithinPreviousOrCurrentGoal(self):
        if self.ruleName in ['sequence', 'audio-guided-sequence', 'protocol5_lv3', 'olfactory_support']:
            position = self.tunnel.position
            # This is equal to subtracting one
            previousGoalIdx = (self.currentGoalIdx +
                               (self.goalNums-1)) % self.goalNums
            for goalIdx in [previousGoalIdx, self.currentGoalIdx]:
                goals = self.goals[goalIdx]
                if position > goals[0] and position < goals[1]:
                    return True
            return False
        else:
            return True

    def handleNextGoal(self):
        if self.ruleName in ['sequence', 'audio-guided-sequence', 'protocol5_lv3', 'olfactory_support']:
            self.currentGoalIdx = (self.currentGoalIdx + 1) % self.goalNums
            print('next goal is set to {}'.format(self.currentGoalIdx))
        elif self.ruleName == 'run-auto' or self.ruleName == 'run-lick':
            self.currentGoal = self.tunnel.position + self.total_forward_run_distance + \
                np.random.randint(
                    10) + self.flip_tunnel_options['reward_distance']
        elif self.ruleName in ['protocol1_lv1', 'protocol1_lv2']:
            while self.currentGoal < self.tunnel.position + self.total_forward_run_distance:
                self.currentGoal += self.flip_tunnel_options['reward_distance']
            self.currentGoal -= self.flip_tunnel_options['reward_distance']
            print('next goal is set to {}'.format(self.currentGoal))
    
    def handleNextLM(self):
        if self.ruleName in ['olfactory_support']:
            self.currentLMIdx = (self.currentLMIdx + 1) % self.LMNums
            print('next landmark is set to {}'.format(self.currentLMIdx))



    def reset_tunnel_task(self, task):
        self.current_flip_sections = []
        for i, section in enumerate(self.flip_sections):
            section.reset()
            self.logger.info('section_id %d, new stim %d', i, section.stim_id)
        self.tunnel.freeze(False)
        self.tunnel.reset_camera(position=self.triggeResetPositionStart)
        self.total_forward_run_distance += self.flip_tunnel_options['corridor_len']

    def stop_valve_task(self, task):
        print('stop valve')
        self.valveController.stop()

    def stop_sound_task(self, task):
        self.sounds[self.sound_mode].stop()
        self.sound_mode = 'correct'

    def stop_assist_sound(self):
        self.sounds[self.currentGoalIdx].stop()
        self.sounds[self.currentGoalIdx].setLoop(False)
        self.sounds[self.currentGoalIdx].setVolume(1)
        self.assist_sound_playing = False

    def stop_main_sound(self):
        self.sounds[self.current_landmark].stop()
        self.sounds[self.current_landmark].setLoop(False)
        self.sounds[self.current_landmark].setVolume(1)
        self.main_sound_playing = False

    def stop_airpuff_task(self, task):
        self.airpuffController.stop()

    def reset_tunnel2end_task(self, task):
        self.current_flip_sections = []
        for i, section in enumerate(self.flip_sections):
            section.reset()
            self.logger.info('section_id %d, new stim %d', i, section.stim_id)
        self.tunnel.freeze(False)
        self.tunnel.reset_camera(position=self.triggeResetPosition)
        self.total_forward_run_distance -= self.flip_tunnel_options['corridor_len']

    def update_tunnel_task(self, task):
        # update grating sections, if mouse is in their onset/offset part
        self.current_flip_sections = []
        card_color = [0, 0, 0, 1]

        for sid, section in enumerate(self.flip_sections):
            if section.update(self.tunnel):
                self.logger.info("section_id: %d, %s", sid, section.flipper)
                self.current_flip_sections.append(section)
                card_color = [1, 1, 1, 1]

        # change card color to indicate stimulus on
        self.card.setColor(*card_color)

        if self.tunnel.position > self.triggeResetPosition and not self.tunnel.frozen:
            self.tunnel.freeze(True)
            self.tunnel.taskMgr.doMethodLater(
                self.sleep_time, self.reset_tunnel_task, 'reset_tunnel_task'
            )

        if self.tunnel.position < self.triggeResetPositionStart and not self.tunnel.frozen:
            self.tunnel.freeze(True)
            self.tunnel.taskMgr.doMethodLater(
                self.sleep_time, self.reset_tunnel2end_task, 'reset_tunnel2end_task'
            )

        return Task.cont

    def create_nidaq_controller(self, options):
        if 'daqChannel' in options:
            import nidaq as nidaq
            self.valveController = nidaq.DigitalOutput(
                options['daqChannel']['valve1'])
            self.lickDetector = nidaq.AnalogInput(
                **options['daqChannel']['spout1'])

            if 'airpuff' in options['daqChannel']:
                self.airpuffController = nidaq.DigitalOutput(
                    options['daqChannel']['airpuff'])
                self.isAirpuff = True
            else:
                self.isAirpuff = False

            #assumption: if odour1 is specified, the full MODD system is
            if 'odour1' in options['daqChannel']:
                self.odourcontroller1 = nidaq.DigitalOutput(
                    options['daqChannel']['odour1'])
                self.odourcontroller2 = nidaq.DigitalOutput(
                    options['daqChannel']['odour2'])
                self.odourcontroller3 = nidaq.DigitalOutput(
                    options['daqChannel']['odour3'])
                self.odourcontroller4 = nidaq.DigitalOutput(
                    options['daqChannel']['odour4'])
                self.odourcontroller5 = nidaq.DigitalOutput(
                    options['daqChannel']['odour5'])
                self.odourcontroller6 = nidaq.DigitalOutput(
                    options['daqChannel']['odour6'])
                self.finalvalve1controller = nidaq.DigitalOutput(
                    options['daqChannel']['finalV1'])
                self.odourcontroller7 = nidaq.DigitalOutput(
                    options['daqChannel']['odour7'])
                self.odourcontroller8 = nidaq.DigitalOutput(
                    options['daqChannel']['odour8'])
                self.odourcontroller9 = nidaq.DigitalOutput(
                    options['daqChannel']['odour9'])
                self.odourcontroller10 = nidaq.DigitalOutput(
                    options['daqChannel']['odour10'])
                self.odourcontroller11 = nidaq.DigitalOutput(
                    options['daqChannel']['odour11'])
                self.odourcontroller12 = nidaq.DigitalOutput(
                    options['daqChannel']['odour12'])
                self.finalvalve2controller = nidaq.DigitalOutput(
                    options['daqChannel']['finalV2'])
                self.fivevalvecontroller = nidaq.DigitalOutput(
                    options['daqChannel']['fiveV'])
                self.threevalvecontroller = nidaq.DigitalOutput(
                    options['daqChannel']['threeV'])
                self.threevalve2controller = nidaq.DigitalOutput(
                    options['daqChannel']['threeV2'])
                self.isOdourStim = True


            self.isNIDaq = True
        else:
            self.logger.warn("no daq channel specified, "
                             "using default channel 0")
            self.isNIDaq = False
            self.isAirpuff = False

    def update_inputs_task(self, task):
        speed = self.tunnel.speed
        if 'speed' in self.inputs:
            speed = self.inputs['speed'].read_float()

        self.logger.info("speed: %f", speed)
        self.tunnel.speed = speed

        return Task.cont

    def update_piezo_task(self, task):

        self.isChallenged = self.lickDetector.read_float()
        # print('self.isChallenged', self.isChallenged)
        return Task.cont

    def update_outputs_task(self, task):
        if 'stim_id' in self.outputs:
            if self.current_flip_sections:
                if len(self.current_flip_sections) > 1:
                    self.logger.warn("multiple sections triggered, "
                                     "reporting stimulus ID of the first one")
                section = self.current_flip_sections[0]
                self.outputs['stim_id'].write_float(section.stim_id + 1)
            else:
                self.outputs['stim_id'].write_float(0)

        if 'position' in self.outputs:
            self.outputs['position'].write_float(self.tunnel.scaled_position)

        if 'speed' in self.outputs:
            self.outputs['speed'].write_float(self.tunnel.speed)

        return Task.cont

    def run(self):
        self.tunnel.run()

    def close(self):
        print('closing flip tunnel')
        print('input', self.inputs.keys())
        print('output', self.outputs.keys())
        print('valve', self.valveController)
        for input in self.inputs.values():
            input.close()
        for output in self.outputs.values():
            output.close()
        self.valveController.close()
        if self.isOdourStim:
            self.odourcontroller1.stop()
            self.odourcontroller2.stop()
            self.odourcontroller3.stop()
            self.odourcontroller4.stop()
            self.odourcontroller5.stop()
            self.odourcontroller6.stop()
            self.finalvalve1controller.stop()
            self.fivevalvecontroller.stop()
            self.threevalvecontroller.stop()
            self.odourcontroller1.close()
            self.odourcontroller2.close()
            self.odourcontroller3.close()
            self.odourcontroller4.close()
            self.odourcontroller5.close()
            self.odourcontroller6.close()
            self.finalvalve1controller.close()
            self.fivevalvecontroller.close()
            self.threevalvecontroller.close()
            self.odourcontroller7.stop()
            self.odourcontroller8.stop()
            self.odourcontroller9.stop()
            self.odourcontroller10.stop()
            self.odourcontroller11.stop()
            self.odourcontroller12.stop()
            self.finalvalve2controller.stop()
            self.threevalve2controller.stop()
            self.odourcontroller7.close()
            self.odourcontroller8.close()
            self.odourcontroller9.close()
            self.odourcontroller10.close()
            self.odourcontroller11.close()
            self.odourcontroller12.close()
            self.finalvalve2controller.close()
            self.threevalve2controller.close()

    def position_logging_task(self, task):
        if not hasattr(task, 'next_log_time'):
            task.next_log_time = self.globalClock.getFrameTime()

        current_time = self.globalClock.getFrameTime()

        if current_time < task.next_log_time:
            return Task.cont

        task.next_log_time += 1.0 / 60.0  # Schedule the next log in 1/60th of a second

        position = self.tunnel.position
        total_run_distance = self.total_forward_run_distance + position
        self.position_writer.writerow(
            [self.sample_i, current_time, position, total_run_distance, ''])

        if self.wasChallenged:
            # print("challenged")
            self.position_writer.writerow(
                [self.sample_i, current_time, -1, -1, "challenged"])
            self.wasChallenged = False

        if self.wasRewarded:
            self.position_writer.writerow(
                [self.sample_i, current_time, -1, -1, "rewarded"])
            print("rewarded")
            self.wasRewarded = False

        if self.wasManuallyRewarded:
            self.position_writer.writerow(
                [self.sample_i, current_time, -1, -1, "manually-rewarded"])
            print("mannualy rewarded")
            self.wasManuallyRewarded = False

        if self.wasAssistRewarded:
            self.position_writer.writerow(
                [self.sample_i, current_time, -1, -1, "assist-rewarded"])
            print("assist rewarded")
            self.wasAssistRewarded = False

        self.sample_i += 1

        return Task.cont

    # def event_logging_task(self, task):
    #     if self.wasChallenged:
    #         print('evemt logger was chalenged')
    #         print([self.shared_timestamp, "challenged"])
    #         self.position_writer.writerow([self.shared_timestamp, -1, "challenged"])
    #         self.event_writer.writerow([self.shared_timestamp, "challenged"])
    #         self.wasChallenged = False

    #     if self.wasRewarded:
    #         print('event logger was rewarded')
    #         self.position_writer.writerow([self.shared_timestamp, -1, "rewarded"])
    #         self.event_writer.writerow([self.shared_timestamp, "rewarded"])
    #         print([self.shared_timestamp, "rewarded"])
    #         self.wasRewarded = False

    #     return Task.cont
