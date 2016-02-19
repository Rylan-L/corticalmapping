__author__ = 'junz'


import os
import numpy as np
import matplotlib.pyplot as plt
from toolbox.misc import BinarySlicer
import warnings
import corticalmapping.core.tifffile as tf
import corticalmapping.core.FileTools as ft
import corticalmapping.core.TimingAnalysis as ta
import corticalmapping.HighLevel as hl
import corticalmapping.RetinotopicMapping as rm



dateRecorded = '160219' # str 'yymmdd'
mouseID = 'TEST' # str, without 'M', for example: '214522'
userID = 'Jun' # user name, should be consistent withe the display log user name
fileNumList = [100] # file number of the imaged movie



dataFolder = r"\\aibsdata2\nc-ophys\CorticalMapping\IntrinsicImageData"
dataFolder = os.path.join(dataFolder,dateRecorded+'-M'+mouseID+'-FlashingCircle')
fileList = os.listdir(dataFolder)

#jphys parameters
jphysDtype = np.dtype('>f')
jphysHeaderLength = 96 # length of the header for each channel
jphysChannels = ('photodiode','read','trigger','visualFrame','video1','video2','runningRef','runningSig','open1','open2')# name of all channels
jphysFs = 10000.

#photodiode signal parameters
pdDigitizeThr=0.9
pdFilterSize=0.01
pdSegmentThr=0.02
smallestInterval=1.

#image read signal parameters
readThreshold = 3.
readOnsetType='raising'
temporalDownSampleRate = 1



saveFolder = os.path.dirname(os.path.realpath(__file__))
os.chdir(saveFolder)

for fileNum in fileNumList:
    movPath = os.path.join(dataFolder, [f for f in fileList if (dateRecorded+'JCamF'+str(fileNum) in f) and ('.npy' in f)][0])

    jphysPath = os.path.join(dataFolder, [f for f in fileList if dateRecorded+'JPhys'+str(fileNum) in f][0])

    _, jphys = ft.importRawNewJPhys(jphysPath,dtype=jphysDtype,headerLength=jphysHeaderLength,channels=jphysChannels,sf=jphysFs)

    pd = jphys['photodiode']

    displayOnsets = hl.segmentMappingPhotodiodeSignal(pd,digitizeThr=pdDigitizeThr,filterSize=pdFilterSize,segmentThr=pdSegmentThr,Fs=jphysFs,smallestInterval=smallestInterval)

    imgFrameTS = ta.getOnsetTimeStamps(jphys['read'],Fs=jphysFs,threshold=readThreshold,onsetType=readOnsetType)

    logPath = hl.findLogPath(date=dateRecorded,mouseID=mouseID,stimulus='FlashingCircle',userID=userID,fileNumber=str(fileNum),displayFolder=dataFolder)

    log = ft.loadFile(logPath)
    refreshRate = float(log['monitor']['refreshRate'])
    preGapDur = log['stimulation']['preGapFrameNum'] / refreshRate
    postGapDur = log['stimulation']['postGapFrameNum'] / refreshRate
    displayDur = log['stimulation']['flashFrame'] / refreshRate

    # print 'preGapDur:',preGapDur
    # print 'postGapDur:',postGapDur
    # print 'displayDur:',displayDur

    aveMov, aveMovNor = hl.getAverageDfMovie(movPath, imgFrameTS, displayOnsets, preGapDur+postGapDur+displayDur, startTime=-preGapDur, temporalDownSampleRate=temporalDownSampleRate)

    tf.imsave(dateRecorded + '_M' + mouseID + '_aveMov_' + str(fileNum) + '.tif', aveMov)
    tf.imsave(dateRecorded + '_M' + mouseID + '_aveMovNor_' + str(fileNum) + '.tif', aveMovNor)
