__author__ = 'junz'

import os
import json
import h5py
import numpy as np
import itertools
import pandas as pd
import scipy.stats as stats
import scipy.sparse as sparse
import scipy.ndimage as ni
import matplotlib.pyplot as plt
import tifffile as tf
from toolbox.misc.slicer import BinarySlicer
#import allensdk_internal.brain_observatory.mask_set as mask_set
import corticalmapping.core.ImageAnalysis as ia
import corticalmapping.core.TimingAnalysis as ta
import corticalmapping.core.PlottingTools as pt
import corticalmapping.core.FileTools as ft
#import corticalmapping.SingleCellAnalysis as sca
import corticalmapping.RetinotopicMapping as rm

#try:
#    # from r_neuropil import NeuropilSubtract as NS
#    from allensdk.brain_observatory.r_neuropil import NeuropilSubtract as NS
#except Exception as e:
    #print('fail to import neural pil subtraction module ...')
    #print(e)



def get_masks_from_caiman(spatial_com, dims, thr=0, thr_method='nrg', swap_dim=False):
    """
    Gets masks of spatial components results generated the by the CaImAn segmentation

    this function is stripped out from the caiman.utils.visualization.get_contours(). only works for 2d spatial
    components.

    Args:
         spatial_com: np.ndarray or sparse matrix, mostly will be the caiman.source_extraction.cnmf.estimates.A
                      2d Matrix of Spatial components, each row is a flattened pixel (order 'F'), each column
                      is a spatial component
         dims: tuple of ints
               Spatial dimensions of movie (row, col)
         thr: scalar between 0 and 1
              Energy threshold for computing contours (default 0.9)
              if thr_method is 'nrg': higher thr will make bigger hole inside the mask
              if thr_method is 'max': (usually does not work very well), higher thr will make smaller mask
                                      near the center.
         thr_method: [optional] string
                     Method of thresholding:
                     'max' sets to zero pixels that have value less than a fraction of the max value
                     'nrg' keeps the pixels that contribute up to a specified fraction of the energy
         swap_dim: if True, flattened 2d array will be reshaped by order 'C', otherwise with order 'F'.
    Returns:
         masks: 3d array, dtype=np.float, spatial component x row x col
    """

    if 'csc_matrix' not in str(type(spatial_com)):
        spatial_com = sparse.csc_matrix(spatial_com)

    if len(spatial_com.shape) != 2:
        raise ValueError('input "spatial_com" should be a 2d array or 2d sparse matrix.')

    n_mask = spatial_com.shape[1]

    if len(dims) != 2:
        raise ValueError("input 'dims' should have two entries: (num_row, num_col).")

    if dims[0] * dims[1] != spatial_com.shape[0]:
        raise ValueError("the product of dims[0] and dims[1] ({} x {}) should be equal to the first dimension "
                         "of the input 'spatial_com'.".format(dims[0], dims[1], spatial_com.shape[0]))

    masks = []

    # # get the center of mass of neurons( patches )
    # cm = com(A, *dims)

    # for each patches
    for i in range(n_mask):
        # we compute the cumulative sum of the energy of the Ath component that has been ordered from least to highest
        patch_data = spatial_com.data[spatial_com.indptr[i]:spatial_com.indptr[i + 1]]
        indx = np.argsort(patch_data)[::-1]
        if thr_method == 'nrg':
            cumEn = np.cumsum(patch_data[indx] ** 2)
            # we work with normalized values
            cumEn /= cumEn[-1]
            Bvec = np.ones(spatial_com.shape[0])
            # we put it in a similar matrix
            Bvec[spatial_com.indices[spatial_com.indptr[i]:spatial_com.indptr[i + 1]][indx]] = cumEn
        else:
            if thr_method != 'max':
                print('Unknown threshold method {}. should be either "max" or "nrg". '
                      'Choosing "max".'.format(thr_method))
            Bvec = np.zeros(spatial_com.shape[0])
            Bvec[spatial_com.indices[spatial_com.indptr[i]:
                                     spatial_com.indptr[i + 1]]] = patch_data / patch_data.max()
        if swap_dim:
            Bmat = np.reshape(Bvec, dims, order='C')
            mask = np.array(spatial_com[:, i].todense().reshape(dims, order='C'))
        else:
            Bmat = np.reshape(Bvec, dims, order='F')
            mask = np.array(spatial_com[:, i].todense().reshape(dims, order='F'))

        Bmat[Bmat >= thr] = 1.
        Bmat[Bmat < thr] = 0.

        masks.append(mask * Bmat)

    return np.array(masks)


def threshold_mask_by_energe(mask, sigma=1., thr_high=0.0, thr_low=0.1):
    """
    threshold a weighted mask by reversed accumulative energy. Use this to treat masks spit out by caiman
    segmentation.
    :param mask: 2d array
    :param sigma: float, 2d gaussian filter sigma
    :param thr_high: float, 0 - 1, bigger thr_high will make bigger hole inside the roi
    :param thr_low: float, 0 - 1, bigger thr_low will make smaller roi around the center
    :return: 2d array thresholded mask
    """

    if len(mask.shape) != 2:
        raise ValueError('input "mask" should be a 2d array.')

    if sigma is not None:
        mask = ni.gaussian_filter(mask, sigma=sigma)

    mask = ia.array_nor(mask)
    mask_s = mask.flatten()

    indx_low = np.argsort(mask_s)
    cum_eng_low = np.cumsum(mask_s[indx_low] ** 2)
    cum_eng_low /= cum_eng_low[-1]
    mask_eng_low = np.ones(mask_s.shape, dtype=np.float)
    mask_eng_low[indx_low] = cum_eng_low
    mask_eng_low = mask_eng_low.reshape(mask.shape)

    indx_high = np.argsort(mask_s)[::-1]
    cum_eng_high = np.cumsum(mask_s[indx_high] ** 2)
    cum_eng_high /= cum_eng_high[-1]
    mask_eng_high = np.ones(mask_s.shape, dtype=np.float)
    mask_eng_high[indx_high] = cum_eng_high
    mask_eng_high = mask_eng_high.reshape(mask.shape)

    mask_bin = np.ones(mask.shape)

    mask_bin[mask_eng_high < thr_high] = 0.
    mask_bin[mask_eng_low < thr_low] = 0.

    mask_labeled, mask_num = ni.label(mask_bin, structure=[[1,1,1], [1,1,1], [1,1,1]])
    mask_dict = ia.get_masks(labeled=mask_labeled, keyPrefix='', labelLength=5)

    for key, value in mask_dict.items():
        mask_w = value * mask
        mask_w = mask_w / np.amax(mask_w)
        mask_dict[key] = ia.WeightedROI(mask_w)

    return mask_dict


def translateMovieByVasculature(mov, parameterPath, matchingDecimation=2, referenceDecimation=2, verbose=True):
    '''

    :param mov: movie before translation (could be 2d (just one frame) or 3d)
    :param parameterPath: path to the json file with translation parameters generated by VasculatureMapMatching GUI
    :param movDecimation: decimation factor from movie vasculature image to movie
    :param mappingDecimation: decimation factor from mapping vasculature image to mapped areas, usually 2
    :return: translated movie
    '''

    with open(parameterPath) as f:
        matchingParams = json.load(f)

    matchingDecimation = float(matchingDecimation);
    referenceDecimation = float(referenceDecimation)

    if matchingParams[
        'Xoffset'] % matchingDecimation != 0: print('Original Xoffset is not divisble by movDecimation. Taking the floor integer.')
    if matchingParams[
        'Yoffset'] % matchingDecimation != 0: print('Original Yoffset is not divisble by movDecimation. Taking the floor integer.')

    offset = [int(matchingParams['Xoffset'] / matchingDecimation),
              int(matchingParams['Yoffset'] / matchingDecimation)]

    if matchingParams[
        'ReferenceMapHeight'] % matchingDecimation != 0: print('Original ReferenceMapHeight is not divisble by movDecimation. Taking the floor integer.')
    if matchingParams[
        'ReferenceMapWidth'] % matchingDecimation != 0: print('Original ReferenceMapWidth is not divisble by movDecimation. Taking the floor integer.')

    outputShape = [int(matchingParams['ReferenceMapHeight'] / matchingDecimation),
                   int(matchingParams['ReferenceMapHeight'] / matchingDecimation)]

    movT = ia.rigid_transform_cv2(mov, zoom=matchingParams['Zoom'], rotation=matchingParams['Rotation'], offset=offset,
                                  outputShape=outputShape)

    if matchingDecimation / referenceDecimation != 1:
        movT = ia.rigid_transform_cv2(movT, zoom=matchingDecimation / referenceDecimation)

    if verbose: print('shape of output movie:', movT.shape)

    return movT


def translateHugeMovieByVasculature(inputPath, outputPath, parameterPath, outputDtype=None, matchingDecimation=2,
                                    referenceDecimation=2, chunkLength=100, verbose=True):
    '''
    translate huge .npy matrix with alignment parameters into another huge .npy matrix without loading everything into memory
    :param inputPath: path of input movie (.npy file)
    :param outputPath: path of output movie (.npy file)
    :param outputDtype: data type of output movie
    :param parameterPath: path to the json file with translation parameters generated by VasculatureMapMatching GUI
    :param matchingDecimation: decimation factor on the matching side (usually 2)
    :param referenceDecimation: decimation factor on the reference side (if using standard retinotopic mapping pkl file, should be 2)
    :param chunkLength: frame number of chunks
    :return:
    '''

    chunkLength = int(chunkLength)

    inputMov = BinarySlicer(inputPath)

    if outputDtype is None: outputDtype = inputMov.dtype.str

    if len(inputMov.shape) != 3: raise ValueError('Input movie should be 3-d!')

    frameNum = inputMov.shape[0]

    if outputPath[-4:] != '.npy': outputPath += '.npy'

    if verbose: print('\nInput movie shape:', inputMov.shape)

    chunkNum = frameNum // chunkLength
    if frameNum % chunkLength == 0:
        if verbose:
            print('Translating in chunks: ' + str(chunkNum) + ' x ' + str(chunkLength) + ' frame(s)')
    else:
        chunkNum += 1
        if verbose: print('Translating in chunks: ' + str(chunkNum - 1) + ' x ' + str(
            chunkLength) + ' frame(s)' + ' + ' + str(frameNum % chunkLength) + ' frame(s)')

    frameT1 = translateMovieByVasculature(inputMov[0, :, :], parameterPath=parameterPath,
                                          matchingDecimation=matchingDecimation,
                                          referenceDecimation=referenceDecimation, verbose=False)
    plt.imshow(frameT1, cmap='gray')
    plt.show()

    if verbose: print('Output movie shape:', (frameNum, frameT1.shape[0], frameT1.shape[1]), '\n')

    with open(outputPath, 'wb') as f:
        np.lib.format.write_array_header_1_0(f, {'descr': outputDtype, 'fortran_order': False,
                                                 'shape': (frameNum, frameT1.shape[0], frameT1.shape[1])})

        for i in range(chunkNum):
            indStart = i * chunkLength
            indEnd = (i + 1) * chunkLength
            if indEnd > frameNum: indEnd = frameNum
            currMov = inputMov[indStart:indEnd, :, :]
            if verbose: print('Translating frame ' + str(indStart) + ' to frame ' + str(indEnd) + '.\t' + str(
                i * 100. / chunkNum) + '%')
            currMovT = translateMovieByVasculature(currMov, parameterPath=parameterPath,
                                                   matchingDecimation=matchingDecimation,
                                                   referenceDecimation=referenceDecimation, verbose=False)
            currMovT = currMovT.astype(outputDtype)
            currMovT.reshape((np.prod(currMovT.shape),)).tofile(f)


def segmentPhotodiodeSignal(pd, digitizeThr=0.9, filterSize=0.01, segmentThr=0.02, Fs=10000.,
                            smallestInterval=10., verbose=False):
    '''

    :param pd: photodiode from mapping jphys file
    :param digitizeThr: threshold to digitize photodiode readings
    :param filterSize: gaussian filter size to filter photodiode signal, sec
    :param segmentThr: threshold to detect the onset of each stimulus sweep
    :param Fs: sampling rate
    :return:
    '''

    pdDigitized = np.array(pd)
    # plt.plot(pdDigitized[0: 100 * 30000])
    # plt.show()

    pdDigitized[pd < digitizeThr] = 0.;
    pdDigitized[pd >= digitizeThr] = 5.
    # plt.plot(pdDigitized[0: 100 * 30000])
    # plt.show()

    filterDataPoint = int(filterSize * Fs)

    pdFiltered = ni.filters.gaussian_filter(pdDigitized, filterDataPoint)
    pdFilteredDiff = np.diff(pdFiltered)
    pdFilteredDiff = np.hstack(([0], pdFilteredDiff))
    pdSignal = np.multiply(pdDigitized, pdFilteredDiff)
    # plt.plot(pdSignal[0: 100 * 30000])
    # plt.show()

    # plt.plot(pdSignal[:1000000])
    # plt.show()
    displayOnsets = ta.get_onset_timeStamps(pdSignal, Fs, threshold=segmentThr, onsetType='raising')

    trueDisplayOnsets = []
    for i, displayOnset in enumerate(displayOnsets):
        if i == 0:
            trueDisplayOnsets.append(displayOnset)
            currOnset = displayOnset
        else:
            if displayOnset - currOnset > smallestInterval:
                trueDisplayOnsets.append(displayOnset)
                currOnset = displayOnset

    print('\nNumber of photodiode onsets:', len(trueDisplayOnsets))

    if verbose:
        print('\nDisplay onsets (sec):')
        print('\n'.join([str(o) for o in trueDisplayOnsets]))

    print('\n')

    return np.array(trueDisplayOnsets)


'''
def getlogPathList(date,#string
                   mouseID,#string
                   stimulus='',#string
                   userID='',#string
                   fileNumber='',#string
                   displayFolder=r'\\W7DTMJ007LHW\data\sequence_display_log'):
    logPathList = []
    for f in os.listdir(displayFolder):
        fn, ext = os.path.splitext(f)
        strings = fn.split('-')
        try: dateTime,stim,mouse,user,fileNum=strings[0:5]
        except Exception as e:
            # print 'Can not read path:',f,'\n',e
            continue
        if (dateTime[0:6] == date) and (mouseID in mouse) and (stimulus in stim) and (userID in user) and (fileNumber == fileNum):
            logPathList.append(os.path.join(displayFolder,f))
    print '\n'+'\n'.join(logPathList)+'\n'
    return logPathList
'''


def findLogPath(date,  # string
                mouseID,  # string
                stimulus='',  # string
                userID='',  # string
                fileNumber='',  # string
                displayFolder=r'\\W7DTMJ007LHW\data\sequence_display_log'):
    logPathList = []
    for f in os.listdir(displayFolder):
        fn, ext = os.path.splitext(f)
        strings = fn.split('-')
        try:
            dateTime, stim, mouse, user, fileNum, trigger, complete = strings
        except Exception as e:
            # print 'Can not read path:',f,'\n',e
            continue
        if (dateTime[0:6] == date) and (mouseID in mouse) and (stimulus in stim) and (userID in user) and (
            fileNumber == fileNum) and (ext == '.pkl'):
            logPathList.append(os.path.join(displayFolder, f))
    print('\n' + '\n'.join(logPathList) + '\n')
    if len(logPathList) == 0:
        raise LookupError('Can not find visual display Log.')
    elif len(logPathList) > 1:
        raise LookupError('Find more than one visual display Log!')
    return logPathList[0]


def getVasMap(vasMapPaths,
              dtype=np.dtype('<u2'),
              headerLength=116,
              tailerLength=218,
              column=1024,
              row=1024,
              frame=1,
              crop=None,
              mergeMethod=np.mean,  # np.median, np.min, np.max
              ):
    vasMaps = []
    for vasMapPath in vasMapPaths:
        currVasMap, _, _ = ft.importRawJCamF(vasMapPath, saveFolder=None, dtype=dtype, headerLength=headerLength,
                                             tailerLength=tailerLength,
                                             column=column, row=row, frame=frame, crop=crop)
        currVasMap[currVasMap > 65534] = np.mean(currVasMap.flat)
        currVasMap[currVasMap < 1] = np.mean(currVasMap.flat)
        vasMaps.append(currVasMap[0].astype(np.float32))
    vasMap = mergeMethod(vasMaps, axis=0)

    return vasMap


'''
def analysisMappingDisplayLogs(logPathList):
    ====================================================================================================================
    :param logFileList: list of paths of all visual display logs of a mapping experiment
    :return:
    B2U: dictionary of all bottom up sweeps
        'ind': indices of these sweeps in the whole experiments
        'startTime': starting time relative to stimulus onset
        'endTime': end time relative to stimulus onset
        'slope': slope of the linear relationship between phase and retinotopic location
        'intercept': intercept of the linear relationship between phase and retinotopic location

    same for U2B, L2R and R2L
    ====================================================================================================================

    displayInfo = {
                   'B2U':{'ind':[],'startTime':[],'sweepDur':[],'slope':[],'intercept':[]},
                   'U2B':{'ind':[],'startTime':[],'sweepDur':[],'slope':[],'intercept':[]},
                   'L2R':{'ind':[],'startTime':[],'sweepDur':[],'slope':[],'intercept':[]},
                   'R2L':{'ind':[],'startTime':[],'sweepDur':[],'slope':[],'intercept':[]}
                   }

    ind=0

    for logPath in sorted(logPathList):
        log = ft.loadFile(logPath)

        #get sweep direction
        direction = log['stimulation']['direction']
        if log['presentation']['displayOrder']==-1: direction=direction[::-1]
        currDict = displayInfo[direction]

        #get number of sweeps
        sweepNum = log['stimulation']['iteration'] * log['presentation']['displayIteration']
        currDict['ind'] += range(ind,ind+sweepNum)
        ind += sweepNum

        #get startTime, sweep duration, phase position relationship
        if not currDict['startTime']:
            refreshRate = float(log['monitor']['refreshRate'])
            interFrameInterval = np.mean(np.diff(log['presentation']['timeStamp']))
            if interFrameInterval > (1.01/refreshRate): raise ValueError, 'Mean visual display too long: '+str(interFrameInterval)+'sec' # check display
            if interFrameInterval < (0.99/refreshRate): raise ValueError, 'Mean visual display too short: '+str(interFrameInterval)+'sec' # check display

            if log['presentation']['displayOrder']==1: currDict['startTime'] = -1 * log['stimulation']['preGapFrame'] / refreshRate
            if log['presentation']['displayOrder']==-1: currDict['startTime'] = -1 * log['stimulation']['postGapFrame'] / refreshRate

            currDict['sweepDur'] = len(log['presentation']['displayFrames']) / (log['stimulation']['iteration'] * log['presentation']['displayIteration'] * refreshRate)

            currDict['slope'], currDict['intercept'] = rm.getPhasePositionEquation(log)

    return displayInfo
'''


def analysisMappingDisplayLog(display_log):
    '''
    :param logFile: log dictionary or the path of visual display log of a mapping experiment
    :return:
    displayInfo: dictionary, for each direction ('B2U','U2B','L2R','R2L'):
        'ind': indices of these sweeps in the whole experiment
        'startTime': starting time relative to stimulus onset
        'sweepDur': duration of the sweep
        'slope': slope of the linear relationship between phase and retinotopic location
        'intercept': intercept of the linear relationship between phase and retinotopic location

    same for U2B, L2R and R2L
    '''

    displayInfo = {
        'B2U': {'ind': [], 'startTime': [], 'sweepDur': [], 'slope': [], 'intercept': []},
        'U2B': {'ind': [], 'startTime': [], 'sweepDur': [], 'slope': [], 'intercept': []},
        'L2R': {'ind': [], 'startTime': [], 'sweepDur': [], 'slope': [], 'intercept': []},
        'R2L': {'ind': [], 'startTime': [], 'sweepDur': [], 'slope': [], 'intercept': []}
    }

    if isinstance(display_log, dict):
        log = display_log
    elif isinstance(display_log, str):
        log = ft.loadFile(display_log)
    else:
        raise ValueError('log should be either dictionary or a path string!')

    def convert(data):
        if isinstance(data, bytes):  return data.decode('ascii')
        if isinstance(data, dict):   return dict(map(convert, data.items()))
        if isinstance(data, tuple):  return map(convert, data)
        return data

    log = convert(log) # convert bytestrings to strings (a py2to3 issue)

    # check display order
    if log['presentation']['displayOrder'] == -1: raise ValueError('Display order is -1 (should be 1)!')
    refreshRate = float(log['monitor']['refreshRate'])

    # check display visual frame interval
    interFrameInterval = np.mean(np.diff(log['presentation']['timeStamp']))
    if interFrameInterval > (1.01 / refreshRate): raise ValueError('Mean visual display too long: ' + str(
        interFrameInterval) + 'sec')  # check display
    if interFrameInterval < (0.99 / refreshRate): raise ValueError('Mean visual display too short: ' + str(
        interFrameInterval) + 'sec')  # check display

    # get sweep start time relative to display onset
    try:
        startTime = -1 * log['stimulation']['preGapDur']
    except KeyError:
        startTime = -1 * log['stimulation']['preGapFrameNum'] / log['monitor']['refreshRate']
    print('Movie chunk start time relative to sweep onset:', startTime, 'sec')
    displayInfo['B2U']['startTime'] = startTime;
    displayInfo['U2B']['startTime'] = startTime
    displayInfo['L2R']['startTime'] = startTime;
    displayInfo['R2L']['startTime'] = startTime

    # get basic information
    frames = log['stimulation']['frames']
    displayIter = log['presentation']['displayIteration']
    sweepTable = log['stimulation']['sweepTable']
    dirList = []
    B2Uframes = [];
    U2Bframes = [];
    L2Rframes = [];
    R2Lframes = []

    # parcel frames for each direction
    for frame in frames:
        currDir = frame[4]
        if currDir not in dirList: dirList.append(currDir)
        if currDir == b'B2U':
            B2Uframes.append(frame)
        elif currDir == b'U2B':
            U2Bframes.append(frame)
        elif currDir == b'L2R':
            L2Rframes.append(frame)
        elif currDir == b'R2L':
            R2Lframes.append(frame)

    # get sweep order indices for each direction
    dirList = dirList * displayIter
    displayInfo['B2U']['ind'] = [ind for ind, adir in enumerate(dirList) if adir == b'B2U']
    print('B2U sweep order indices:', displayInfo['B2U']['ind'])
    displayInfo['U2B']['ind'] = [ind for ind, adir in enumerate(dirList) if adir == b'U2B']
    print('U2B sweep order indices:', displayInfo['U2B']['ind'])
    displayInfo['L2R']['ind'] = [ind for ind, adir in enumerate(dirList) if adir == b'L2R']
    print('L2R sweep order indices:', displayInfo['L2R']['ind'])
    displayInfo['R2L']['ind'] = [ind for ind, adir in enumerate(dirList) if adir == b'R2L']
    print('R2L sweep order indices:', displayInfo['R2L']['ind'])

    # get sweep duration for each direction
    displayInfo['B2U']['sweepDur'] = len(B2Uframes) / refreshRate
    print('Chunk duration for B2U sweeps:', displayInfo['B2U']['sweepDur'], 'sec')
    displayInfo['U2B']['sweepDur'] = len(U2Bframes) / refreshRate
    print('Chunk duration for U2B sweeps:', displayInfo['U2B']['sweepDur'], 'sec')
    displayInfo['L2R']['sweepDur'] = len(L2Rframes) / refreshRate
    print('Chunk duration for L2R sweeps:', displayInfo['L2R']['sweepDur'], 'sec')
    displayInfo['R2L']['sweepDur'] = len(R2Lframes) / refreshRate
    print('Chunk duration for R2L sweeps:', displayInfo['R2L']['sweepDur'], 'sec')

    # get phase position slopes and intercepts for each direction
    displayInfo['B2U']['slope'], displayInfo['B2U']['intercept'] = rm.getPhasePositionEquation2(B2Uframes, sweepTable)
    displayInfo['U2B']['slope'], displayInfo['U2B']['intercept'] = rm.getPhasePositionEquation2(U2Bframes, sweepTable)
    displayInfo['L2R']['slope'], displayInfo['L2R']['intercept'] = rm.getPhasePositionEquation2(L2Rframes, sweepTable)
    displayInfo['R2L']['slope'], displayInfo['R2L']['intercept'] = rm.getPhasePositionEquation2(R2Lframes, sweepTable)

    return displayInfo


def analyzeSparseNoiseDisplayLog(logPath):
    '''
    return the indices of visual display frames for each square in a sparse noise display

    return:
    allOnsetInd: the indices of frames for each square, list
    onsetIndWithLocationSign: indices of squares for each location and sign,
                              list with element structure [np.array([alt, azi]),sign,[list of indices of square onset]]
    '''

    log = ft.loadFile(logPath)

    if log['stimulation']['stimName'] != 'SparseNoise':
        raise LookupError('The stimulus type should be sparse noise!')

    frames = log['presentation']['displayFrames']
    frames = [tuple([np.array([x[1][1], x[1][0]]), x[2], x[3], i]) for i, x in enumerate(frames)]
    dtype = [('location', np.ndarray), ('sign', int), ('isOnset', int), ('index', int)]
    frames = np.array(frames, dtype=dtype)

    allOnsetInd = []
    for i in range(len(frames)):
        if frames[i]['isOnset'] == 1 and (i == 0 or frames[i - 1]['isOnset'] == -1):
            allOnsetInd.append(i)

    onsetFrames = frames[allOnsetInd]

    allSquares = list(set([tuple([x[0][0], x[0][1], x[1]]) for x in onsetFrames]))

    onsetIndWithLocationSign = []

    for square in allSquares:
        indices = []
        for j, onsetFrame in enumerate(onsetFrames):
            if onsetFrame['location'][0] == square[0] and onsetFrame['location'][1] == square[1] and onsetFrame[
                'sign'] == square[2]:
                indices.append(j)

        onsetIndWithLocationSign.append([np.array([square[0], square[1]]), square[2], indices])

    return allOnsetInd, onsetIndWithLocationSign


def getAverageDfMovie(movPath, frameTS, onsetTimes, chunkDur, startTime=0., temporalDownSampleRate=1,
                      is_load_all=True):
    '''
    :param movPath: path to the image movie
    :param frameTS: the timestamps for each frame of the raw movie
    :param onsetTimes: time stamps of onset of each sweep
    :param startTime: chunck start time relative to the sweep onset time (length of pre gray period)
    :param chunkDur: duration of each chunk
    :param temporalDownSampleRate: decimation factor in time after recording
    :return: averageed movie of all chunks
    '''

    if temporalDownSampleRate == 1:
        frameTS_real = frameTS
    elif temporalDownSampleRate > 1:
        frameTS_real = frameTS[::temporalDownSampleRate]
    else:
        raise ValueError('temporal downsampling rate can not be less than 1!')

    if is_load_all:
        if movPath[-4:] == '.npy':
            try:
                mov = np.load(movPath)
            except ValueError:
                print('Cannot load the entire npy file into memroy. Trying BinarySlicer...')
                mov = BinarySlicer(movPath)
        elif movPath[-4:] == '.tif':
            mov = tf.imread(movPath)
        else:
            mov, _, _ = ft.importRawJCamF(movPath)
    else:
        mov = BinarySlicer(movPath)

    aveMov = ia.get_average_movie(mov, frameTS_real, onsetTimes + startTime, chunkDur)

    meanFrameDur = np.mean(np.diff(frameTS_real))
    baselineFrameDur = int(abs(startTime) / meanFrameDur)

    baselinePicture = np.mean((aveMov[0:baselineFrameDur, :, :]).astype(np.float32), axis=0)
    _, aveMovNor, _ = ia.normalize_movie(aveMov, baselinePicture)

    return aveMov, aveMovNor


def getAverageDfMovieFromH5Dataset(dset, frameTS, onsetTimes, chunkDur, startTime=0., temporalDownSampleRate=1):
    '''
    :param dset: hdf5 dataset object, 3-d matrix, zyx
    :param frameTS: the timestamps for each frame of the raw movie
    :param onsetTimes: time stamps of onset of each sweep
    :param startTime: chunck start time relative to the sweep onset time (length of pre gray period)
    :param chunkDur: duration of each chunk
    :param temporalDownSampleRate: decimation factor in time after recording
    :return: aveMov:  3d array, zyx, float32, averageed movie of all chunks
             n: int, number of chunks averaged
             baseLinePicture: 2d array, float32, baseline picture, None if startTime < 0.
             ts: 1d array, float32, timestamps relative to onsets of the averge movie
    '''

    if temporalDownSampleRate == 1:
        frameTS_real = frameTS
    elif temporalDownSampleRate > 1:
        frameTS_real = frameTS[::temporalDownSampleRate]
    else:
        raise ValueError('temporal downsampling rate can not be less than 1!')

    aveMov, n = ia.get_average_movie(dset, frameTS_real, onsetTimes + startTime, chunkDur, isReturnN=True)

    meanFrameDur = np.mean(np.diff(frameTS_real))
    ts = startTime + np.arange(aveMov.shape[0]) * meanFrameDur

    if startTime < 0.:
        baselineFrameDur = int(abs(startTime) / meanFrameDur)
        baselinePicture = np.mean((aveMov[0:baselineFrameDur, :, :]).astype(np.float32), axis=0)
    else:
        baselinePicture = None

    return aveMov.astype(np.float32), n, baselinePicture.astype(np.float32), ts.astype(np.float32)


def getMappingMovies(movPath, frameTS, displayOnsets, displayInfo, temporalDownSampleRate=1, saveFolder=None,
                     savePrefix='',
                     FFTmode='peak', cycles=1, isRectify=False, is_load_all=False):
    '''

    :param movPath: path of total movie with all directions
    :param frameTS: time stamps of imaging frame
    :param displayOnsets: onset timing of selected chunk
    :param displayInfo: display information generated by the 'analysisMappingDisplayLog'
    :param temporalDownSampleRate: temporal down sample rate during decimation
    :param saveFolder: folder path to save averaged movies
    :param savePrefix: prefix of file name
    :param FFTmode: FFT detect peak or valley, takes 'peak' or 'valley'
    :param cycles: how many cycles in each chunk
    :param isRectify: if True, the fft will be done on the rectified normalized movie, anything below zero will be assigned as zero
    :param is_load_all: load the whole movie into memory or not
    :return: altPosMap,aziPosMap,altPowerMap,aziPowerMap
    '''
    maps = {}

    if FFTmode == 'peak':
        isReverse = False
    elif FFTmode == 'valley':
        isReverse = True
    else:
        raise LookupError('FFTmode should be either "peak" or "valley"!')

    for dir in ['B2U', 'U2B', 'L2R', 'R2L']:
        print('\nAnalyzing sweeps with direction:', dir)

        onsetInd = list(displayInfo[dir]['ind'])

        for ind in displayInfo[dir]['ind']:
            if ind >= len(displayOnsets):
                print('Visual Stimulation Direction:' + dir + ' index:' + str(
                    ind) + ' was not displayed. Remove from averageing.')
                onsetInd.remove(ind)

        aveMov, aveMovNor = getAverageDfMovie(movPath=movPath,
                                              frameTS=frameTS,
                                              onsetTimes=displayOnsets[onsetInd],
                                              chunkDur=displayInfo[dir]['sweepDur'],
                                              startTime=displayInfo[dir]['startTime'],
                                              temporalDownSampleRate=temporalDownSampleRate,
                                              is_load_all=is_load_all)

        print('aveMov.shape = ' + str(aveMov.shape))
        print('aveMovNor.shape = ' + str(aveMovNor.shape))

        if isRectify:
            aveMovNorRec = np.array(aveMovNor)
            aveMovNorRec[aveMovNorRec < 0.] = 0.
            phaseMap, powerMap = rm.generatePhaseMap2(aveMovNorRec, cycles, isReverse)
        else:
            phaseMap, powerMap = rm.generatePhaseMap2(aveMov, cycles, isReverse)

        powerMap = powerMap / np.amax(powerMap)
        positionMap = phaseMap * displayInfo[dir]['slope'] + displayInfo[dir]['intercept']
        maps.update({'posMap_' + dir: positionMap,
                     'powerMap_' + dir: powerMap})

        if saveFolder is not None:
            if savePrefix:
                tf.imsave(os.path.join(saveFolder, savePrefix + '_aveMov_' + dir + '.tif'), aveMov.astype(np.float32))
                tf.imsave(os.path.join(saveFolder, savePrefix + '_aveMovNor_' + dir + '.tif'),
                          aveMovNor.astype(np.float32))
            else:
                tf.imsave(os.path.join(saveFolder, savePrefix + 'aveMov_' + dir + '.tif'), aveMov.astype(np.float32))
                tf.imsave(os.path.join(saveFolder, savePrefix + 'aveMovNor_' + dir + '.tif'),
                          aveMovNor.astype(np.float32))

    altPosMap = np.mean([maps['posMap_B2U'], maps['posMap_U2B']], axis=0)
    aziPosMap = np.mean([maps['posMap_L2R'], maps['posMap_R2L']], axis=0)

    altPowerMap = np.mean([maps['powerMap_B2U'], maps['powerMap_U2B']], axis=0)
    altPowerMap = altPowerMap / np.amax(altPowerMap)

    aziPowerMap = np.mean([maps['powerMap_L2R'], maps['powerMap_L2R']], axis=0)
    aziPowerMap = aziPowerMap / np.amax(aziPowerMap)

    return altPosMap, aziPosMap, altPowerMap, aziPowerMap


def regression_detrend(mov, roi, verbose=True):
    """
    detrend a movie by subtracting global trend as average activity in side the roi. It work on a pixel by pixel bases
    and use linear regress to determine the contribution of the global signal to the pixel activity.

    ref:
    1. J Neurosci. 2016 Jan 27;36(4):1261-72. doi: 10.1523/JNEUROSCI.2744-15.2016. Resolution of High-Frequency
    Mesoscale Intracortical Maps Using the Genetically Encoded Glutamate Sensor iGluSnFR. Xie Y, Chan AW, McGirr A,
    Xue S, Xiao D, Zeng H, Murphy TH.
    2. Neuroimage. 1998 Oct;8(3):302-6. The inferential impact of global signal covariates in functional neuroimaging
    analyses. Aguirre GK1, Zarahn E, D'Esposito M.

    :param mov: input movie
    :param roi: binary, weight and binaryNan roi to define global signal
    :return: detrended movie, trend, amp_map, rvalue_map
    """

    if len(mov.shape) != 3:
        raise ValueError

    roi = ia.WeightedROI(roi)
    trend = roi.get_weighted_trace(mov)

    mov_new = np.empty(mov.shape, dtype=np.float32)
    slopes = np.empty((mov.shape[1], mov.shape[2]), dtype=np.float32)
    rvalues = np.empty((mov.shape[1], mov.shape[2]), dtype=np.float32)

    pixel_num = mov.shape[1] * mov.shape[2]

    n = 0

    for i, j in itertools.product(list(range(mov.shape[1])), list(range(mov.shape[2]))):
        pixel_trace = mov[:, i, j]
        slope, intercept, r_value, p_value, stderr = stats.linregress(trend, pixel_trace)
        slopes[i, j] = slope
        rvalues[i, j] = r_value
        mov_new[:, i, j] = pixel_trace - trend * slope

        if verbose:
            if n % (pixel_num // 10) == 0:
                print('progress:', int(round(float(n) * 100 / pixel_num)), '%')
        n += 1

    return mov_new, trend, slopes, rvalues


def neural_pil_subtraction(trace_center, trace_surround, lam=0.05):
    """
    use allensdk neural pil subtraction

    :param trace_center: input center trace
    :param trace_surround: input surround trace
    :param lam:
    :return:
        r: contribution of the surround to the center
        error: final cross-validation error
        trace: trace after neuropil subtracction
    """

    if trace_center.shape != trace_surround.shape:
        raise ValueError('center trace and surround trace should have same shape')

    if len(trace_center.shape) != 1:
        raise ValueError('input traces should be 1 dimensional!')

    trace_center = trace_center.astype(np.float)
    trace_surround = trace_surround.astype(np.float)

    ns = NS(lam=lam)

    # ''' normalize to have F_N in (0,1)'''
    # F_M = (trace_center - float(np.amin(trace_surround))) / float(np.amax(trace_surround) - np.amin(trace_surround))
    # F_N = (trace_surround - float(np.amin(trace_surround))) / float(np.amax(trace_surround) - np.amin(trace_surround))

    '''fitting model'''
    # ns.set_F(F_M, F_N)
    ns.set_F(trace_center, trace_surround)

    '''stop gradient descent at first increase of cross-validation error'''
    ns.fit()
    # ns.fit_block_coordinate_desc()

    return ns.r, ns.error, trace_center - ns.r * trace_surround


def get_lfp(trace, fs=30000., notch_base=60., notch_bandwidth=1., notch_harmonics=4, notch_order=2,
            lowpass_cutoff=300., lowpass_order=5):
    """

    :param trace: 1-d array, input trace
    :param fs: float, sampling rate, Hz
    :param notch_base: float, Hz, base frequency of powerline contaminating signal
    :param notch_bandwidth: float, Hz, filter bandwidth at each side of center frequency
    :param notch_harmonics: int, number of harmonics to filter out
    :param notch_order: int, order of butterworth bandpass notch filter, for a narrow band, shouldn't be larger than 2
    :param lowpass_cutoff: float, Hz, cutoff frequency of lowpass filter
    :param lowpass_order: int, order of butterworth lowpass filter
    :return: filtered LFP, 1-d array with same dtype as input trace
    """

    trace_float = trace.astype(np.float32)
    trace_notch = ta.notch_filter(trace_float, fs=fs, freq_base=notch_base, bandwidth=notch_bandwidth,
                                  harmonics=notch_harmonics, order=notch_order)
    lfp = ta.butter_lowpass(trace_notch, fs=fs, cutoff=lowpass_cutoff, order=lowpass_order)

    return lfp.astype(trace.dtype)


def array_to_rois(input_folder, overlap_threshold=0.9, neuropil_limit=(5, 10), is_plot=False):
    """
    generate arrays of rois and their neuropil surround masks from Leonard's segmentation results.
    :param input_folder: the folder that contains Leonard's segmentation results, there should be a file with name
                         'maxInt_masks2.tif' containing information about rois. This is a 3-d array,
                         frame by y by x, each frame should have the same pixel resolution as 2-p image frame.
                         Continuous Non-zero regions in each frame represent roi masks. Each frame contains a set of
                         non-overlapping rois.
    :param overlap_threshold: float, within (0., 1.], allensdk_internal.brain_observatory.mask_set.MaskSet class to
                              detect and remove overlap roi
    :param neuropil_limit: tuple of 2 positive integers, the inner and outer dilation iteration numbers to mark
                           neuropil mask for each center roi
    :return: center_mask_array: 3-d array, np.uint8, each frame is a binary mask for a single center roi
             neuropil_mask_array: 3-d array, np.uint8, each frame is a binary mask of surrounding neuropil region for
                                  a single center roi
    """

    mask_frames = tf.imread(os.path.join(input_folder, 'maxInt_masks2.tif'))

    center_masks = {}

    for i in range(mask_frames.shape[0]):
        curr_mask_frame = mask_frames[i]
        curr_mask_frame[curr_mask_frame > 0] = 1
        curr_labeled, curr_n = ni.label(curr_mask_frame)
        curr_masks = ia.get_masks(curr_labeled, isSort=False, keyPrefix='masks_layer_' + str(i), labelLength=None)
        center_masks.update(curr_masks)

    center_mask_array = []
    for mask in list(center_masks.values()):
        center_mask_array.append(mask)
    center_mask_array = np.array(center_mask_array, dtype=np.uint8)

    #  remvoe duplicates
    ms = mask_set.MaskSet(center_mask_array.astype(bool))
    duplicates = ms.detect_duplicates(overlap_threshold=overlap_threshold)
    # print 'number of duplicates:', len(duplicates)
    if len(duplicates) > 0:
        inds = list(duplicates.keys())
        center_mask_array = np.array([center_mask_array[i] for i in range(len(center_mask_array)) if i not in inds])

    # removing unions
    ms = mask_set.MaskSet(masks=center_mask_array.astype(bool))
    unions = ms.detect_unions()
    # print 'number of unions:', len(unions)
    if len(unions) > 0:
        inds = list(unions.keys())
        center_mask_array = np.array([center_mask_array[i] for i in range(len(center_mask_array)) if i not in inds])

    # get total mask
    total_mask = np.sum(center_mask_array, axis=0).astype(np.bool)
    total_mask = np.logical_not(total_mask).astype(np.uint8)

    neuropil_mask_array = []
    for center_mask in center_mask_array:
        curr_surround = ni.binary_dilation(center_mask, iterations=neuropil_limit[1]) - \
                        ni.binary_dilation(center_mask, iterations=neuropil_limit[0])
        curr_surround = np.logical_and(curr_surround, total_mask)
        neuropil_mask_array.append(curr_surround)
    neuropil_mask_array = np.array(neuropil_mask_array, dtype=np.uint8)

    center_areas = np.sum(np.sum(center_mask_array, axis=-1), axis=-1)
    if np.min(center_areas) == 0:
        raise ValueError('smallest center masks has no pixels.')

    neuropil_areas = np.sum(np.sum(neuropil_mask_array, axis=-1), axis=-1)
    if np.min(neuropil_areas) == 0:
        raise ValueError('smallest neuropil masks has no pixels.')

    if is_plot:
        colors = pt.random_color(center_mask_array.shape[0])
        f = plt.figure(figsize=(15, 7))
        ax1 = f.add_subplot(121)
        ax2 = f.add_subplot(122)

        for i in range(center_mask_array.shape[0]):
            pt.plot_mask_borders(center_mask_array[i], plotAxis=ax1, color=colors[i], borderWidth=1)
            pt.plot_mask_borders(neuropil_mask_array[i], plotAxis=ax2, color=colors[i], borderWidth=1)

        ax1.set_title('center masks')
        ax2.set_title('neuropil masks')
        ax1.set_axis_off()
        ax2.set_axis_off()

        f2 = plt.figure(figsize=(15, 7))
        ax3 = f2.add_subplot(121)
        ax3.hist(center_areas, bins=30)
        ax3.set_title('center mask area distribution')
        ax4 = f2.add_subplot(122)
        ax4.hist(neuropil_areas, bins=30)
        ax4.set_title('neuropil mask area distribution')

        plt.show()

    return center_mask_array, neuropil_mask_array


def concatenate_nwb_files(path_list, save_path, gap_dur=100., roi_path=None, is_save_running=False, analog_chs=(),
                          unit_groups=(), time_series_paths=()):
    """
    concatenate multiple nwb files in to one compact hdf5 file

    :param path_list: list of strings, list of paths of nwb filed to be concatenated
    :param save_path: string, path for the saved hdf5 file
    :param gap_dur: float, in seconds, gap duration between each file
    :param roi_path: string, hdf5 path to roi segmentation
    :param is_save_running: bool, if True, running information will be saved
    :param analog_chs: list of strings, name of analog channels to be concatenated, should be in
                       '/acquisition/timeseries'
    :param unit_groups: list of strings, group names for each set of ephys units, should be in '/processing/'
    :param time_series_paths: list of strings, hdf5 paths to each timeseries to be concatenated, these timeseries
                              should have 'timestamps' field as their timestamps, not 'starting_time' and 'rate' as
                              timestamps
    :return: None
    """

    save_f = h5py.File(save_path)

    fs = None  # analog sampling rate

    if roi_path is not None:
        roi_dict_center = {}
        roi_dict_surround = {}

    next_start = 0.

    analog_dict = {}
    for analog_ch in analog_chs:
        analog_dict.update({analog_ch: []})

    if is_save_running:
        analog_dict.update({'running': []})

    if unit_groups:
        unit_dict = {}
        for unit_group in unit_groups:
            unit_dict.update({unit_group: {}})

    if time_series_paths:
        time_series_dict = {}
        for ts_path in time_series_paths:
            ts_n = os.path.split(ts_path)[1]
            time_series_dict.update({ts_n: {'data': [],
                                            'timestamps': []}})

    for i, curr_path in enumerate(path_list):

        print(('\n\nprocessing ' + curr_path + '...'))

        curr_f = h5py.File(curr_path, 'r')

        # handle sampling rate
        if i == 0:  # first file
            fs = curr_f['general/extracellular_ephys/sampling_rate'].value
        else:
            if fs != curr_f['general/extracellular_ephys/sampling_rate'].value:
                raise ValueError('ephys sampling rate are different.')

        # handle rois
        if (roi_path is not None) and (i == 0):

            try:
                pixel_size = curr_f['acquisition/timeseries/2p_movie/pixel_size'].value
            except Exception:
                pixel_size = None

            try:
                pixel_size_unit = curr_f['acquisition/timeseries/2p_movie/pixel_size_unit'].value
            except Exception:
                pixel_size_unit = None

            roi_grp = curr_f[roi_path]
            for roi_n in list(roi_grp.keys()):
                if roi_n[0: 4] == 'roi_' and roi_n != 'roi_list':
                    roi_dict_center.update({roi_n: ia.ROI(roi_grp[roi_n]['img_mask'].value, pixelSize=pixel_size,
                                                          pixelSizeUnit=pixel_size_unit)})
                elif roi_n[0: 9] == 'surround_':
                    roi_dict_surround.update({roi_n: ia.ROI(roi_grp[roi_n]['img_mask'].value, pixelSize=pixel_size,
                                                            pixelSizeUnit=pixel_size_unit)})
                else:
                    # print('avoid loading {}/{} as an roi.'.format(roi_path, roi_n))
                    print(('avoid loading {} as an roi.'.format(roi_n)))

        # check analog start time
        all_chs_grp = curr_f['acquisition/timeseries']
        for curr_chn, curr_ch_grp in list(all_chs_grp.items()):
            if 'starting_time' in list(curr_ch_grp.keys()):
                total_analog_sample_count = curr_ch_grp['num_samples'].value
                if curr_ch_grp['starting_time'].value != 0:
                    raise ValueError('starting time of analog channel: {} is not 0.'.format(curr_chn))

        # handle analog channels
        for analog_ch in analog_chs:
            curr_analog_trace = curr_f['acquisition/timeseries'][analog_ch]['data'].value.astype(np.float32) * \
                                curr_f['acquisition/timeseries'][analog_ch]['data'].attrs['conversion']
            analog_gap = np.zeros(int(gap_dur * fs), dtype=np.float32)
            analog_gap[:] = np.nan
            analog_dict[analog_ch] += [curr_analog_trace, analog_gap]

        # handle running signals
        if is_save_running:
            curr_running_sig_trace = curr_f['acquisition/timeseries']['running_sig']['data']. \
                                         value.astype(np.float32) * \
                                     curr_f['acquisition/timeseries']['running_sig']['data'].attrs['conversion']

            try:
                curr_running_ref_trace = curr_f['acquisition/timeseries']['running_ref']['data']. \
                                             value.astype(np.float32) * \
                                         curr_f['acquisition/timeseries']['running_ref']['data']. \
                                             attrs['conversion']
            except KeyError:
                curr_running_ref_trace = curr_f['acquisition/timeseries']['Running_ref']['data']. \
                                             value.astype(np.float32) * \
                                         curr_f['acquisition/timeseries']['Running_ref']['data']. \
                                             attrs['conversion']
            curr_running_trace = curr_running_sig_trace - curr_running_ref_trace
            curr_running_gap = np.zeros(int(gap_dur * fs), dtype=np.float32)
            curr_running_gap[:] = np.nan
            analog_dict['running'] += [curr_running_trace, curr_running_gap]

        # handle ephys units
        if unit_groups:
            for curr_ug in unit_groups:
                curr_ug_dict = unit_dict[curr_ug]
                curr_ug_grp = curr_f['processing'][curr_ug]['UnitTimes']
                curr_unit_ns = list(curr_ug_grp['unit_list'].value)
                try:
                    curr_unit_ns.remove('unit_aua')
                except Exception:
                    pass
                for curr_unit_n in curr_unit_ns:
                    if curr_unit_n in list(curr_ug_dict.keys()):
                        curr_ug_dict[curr_unit_n].append(curr_ug_grp[curr_unit_n]['times'].value + next_start)
                    else:
                        curr_ug_dict[curr_unit_n] = [curr_ug_grp[curr_unit_n]['times'].value + next_start]

        if time_series_paths:
            for curr_ts_path in time_series_paths:
                curr_ts_n = os.path.split(curr_ts_path)[1]
                curr_ts_data = curr_f[curr_ts_path]['data'].value
                curr_ts_ts = curr_f[curr_ts_path]['timestamps'].value
                time_series_dict[curr_ts_n]['data'].append(curr_ts_data)
                time_series_dict[curr_ts_n]['timestamps'].append(curr_ts_ts + next_start)

        next_start = next_start + gap_dur + float(total_analog_sample_count) / fs
        curr_f.close()

    fs_dset = save_f.create_dataset('sampling_rate', data=fs)
    fs_dset.attrs['unit'] = 'Hz'

    if roi_path is not None:
        print('\nsaving rois ...')
        roi_grp = save_f.create_group('rois')
        if roi_dict_center:
            for curr_roi_n_c, curr_roi_c in list(roi_dict_center.items()):
                curr_roi_grp = roi_grp.create_group(curr_roi_n_c)
                curr_roi_grp_c = curr_roi_grp.create_group('center')
                curr_roi_c.to_h5_group(curr_roi_grp_c)

                try:
                    curr_roi_n_s = 'surround_' + curr_roi_n_c[4:]
                    curr_roi_s = roi_dict_surround[curr_roi_n_s]
                    curr_roi_grp_s = curr_roi_grp.create_group('surround')
                    curr_roi_s.to_h5_group(curr_roi_grp_s)
                except Exception as e:
                    print(('error in saving surrounding roi: {}. \nError message: {}'.format(curr_roi_n_c, e)))

    if analog_chs or is_save_running:
        print('\nsaving analog channels ...')
        for curr_analog_n, curr_analog_trace_list in list(analog_dict.items()):
            print(curr_analog_n)
            curr_analog_trace_all = np.concatenate(curr_analog_trace_list, axis=0)
            save_f['analog_' + curr_analog_n] = curr_analog_trace_all

    if unit_groups:
        for unit_group in unit_groups:
            print(('\nsaving ephys units for unit group: {}'.format(unit_group)))
            save_units_grp = save_f.create_group(unit_group)
            for unit_n, unit_ts in list(unit_dict[unit_group].items()):
                save_unit_grp = save_units_grp.create_group(unit_n)
                save_unit_grp.create_dataset('timestamps', data=np.concatenate(unit_ts, axis=0))

    if time_series_paths:
        print('\nsaving other time series ...')
        for save_ts_n, save_ts_dict in list(time_series_dict.items()):
            print(save_ts_n)
            save_ts_grp = save_f.create_group(save_ts_n)
            curr_ts_data = save_ts_dict['data']

            # try:
            #     curr_ts_data_con = np.concatenate(curr_ts_data, axis=0)
            # except ValueError:
            #     print('data shape is not aligned for concatenation. Try concatenate in next axis.')
            #     curr_ts_data_t = [ctd.transpose() for ctd in curr_ts_data]
            #     curr_ts_data_con = np.concatenate(curr_ts_data_t, axis=0)

            curr_ts_data_t = [ctd.transpose() for ctd in curr_ts_data]
            curr_ts_data_con = np.concatenate(curr_ts_data_t, axis=0)
            save_ts_grp['data'] = curr_ts_data_con
            save_ts_grp['timestamps'] = np.concatenate(save_ts_dict['timestamps'], axis=0)

    save_f.close()


def plot_sorted_sta_dff(sta_f, sta_t, plot_ax, sort_order, aspect=1.6, is_calculate_dff=True, **kwargs):
    """
    plot sorted spike triggered local df/f matrix of given trigger unit for all rois, it calculates local df/f traces
    from input fluorescence traces.

    :param sta_f: 2d array, t x roi, spike triggered average demixed fluorescence
    :param sta_t: 1d array, timestamps of sta
    :param sort_order: 1d array, length should equal sta_f.shape[1]
    :param plot_ax:
    :param aspect: aspect ratio of the plot
    :param is_calculate_dff: bool, calculate df/f from input trace or not
    :param kwargs: plotting inputs
    :return:
    """

    bl_frame_num = np.sum((sta_t <= 0.).astype(np.int))
    sta_dff = sta_f.transpose()

    if is_calculate_dff:
        bl = np.array([np.mean(sta_dff[:, 0: bl_frame_num], axis=1)]).transpose()
        sta_dff = (sta_dff - bl) / bl

    res_mat = sta_dff[:, bl_frame_num:]
    res_t = sta_t[bl_frame_num:]
    peak_t_ind = np.argmax(res_mat, axis=1)
    peak_time = res_t[peak_t_ind]

    peak_time_sorted = peak_time[sort_order]
    sta_dff_sorted = sta_dff[sort_order]

    curr_fig = plot_ax.imshow(sta_dff_sorted, **kwargs)
    plot_ax.set_aspect(aspect * sta_dff_sorted.shape[1] / sta_dff_sorted.shape[0])
    plot_ax.set_xticks([0., np.argmin(np.abs(sta_t)), sta_dff_sorted.shape[1] - 1.])
    end_t = np.round((sta_t[-1] + np.mean(np.diff(sta_t))) * 100.) / 100.
    plot_ax.set_xticklabels([sta_t[0], 0, end_t])
    plot_ax.set_xlabel('time (sec)')
    plot_ax.axvline(x=np.argmin(np.abs(sta_t)), ls='--', color='k', lw=1)

    for roi_ind, roi_peak_time in enumerate(peak_time_sorted):
        relative_x = float(sta_dff_sorted.shape[1]) * (roi_peak_time - sta_t[0]) / (sta_t[-1] - sta_t[0])
        plot_ax.plot(relative_x, roi_ind, '.k', ms=3)

    plot_ax.set_xlim([0, sta_dff_sorted.shape[1] - 1])
    plot_ax.set_ylim([-0.5, sta_dff_sorted.shape[0] - 0.5])
    plot_ax.invert_yaxis()

    return curr_fig


def get_sort_order(sta_f, sta_t):
    """
    return the sort order of rois based on their peak response time.  it calculates local df/f traces from input
    fluorescence traces.

    :param sta_f: 2d array, t x roi, spike triggered average demixed fluorescence
    :param sta_t: 1d array, timestamps of sta
    :return: 1d array, length equals sta_f.shape[1]
    """

    bl_frame_num = np.sum((sta_t <= 0.).astype(np.int))
    sta_dff = sta_f.transpose()
    bl = np.array([np.mean(sta_dff[:, 0: bl_frame_num], axis=1)]).transpose()
    sta_dff = (sta_dff - bl) / bl

    res_mat = sta_dff[:, bl_frame_num:]
    res_t = sta_t[bl_frame_num:]
    peak_t_ind = np.argmax(res_mat, axis=1)
    peak_time = res_t[peak_t_ind]
    sort_order = np.argsort(peak_time)

    return sort_order


def get_drifting_grating_dataframe(dgr_grp, sweep_dur):
    """
    return a comprehensive dataframe representation of responses to a set of drifting gratings

    :param dgr_grp: hdf5 group object. this object should have an attribute 'time_axis_sec' indicating timestamps of
                    the traces. It should also contain a set of dataset with names 'grating_00000', 'grating_00001',
                    etc, each dataset is a 2d array, trial x time points. The values are number of spikes in each bin
                    each dataset should have attributies: 'contrast' for contrast, from 0. to 1.; 'direction_arc' for
                    direction, 'radius_deg': for radius, 'sf_cyc_per_deg': for spatial frequency, 'tf_hz': for temporal
                    frequency.
    :param sweep_dur: float, the duration of the drifting grating sweep, second
    :return: a dataframe, each row is a unique drifting grating condition, columns: 'sf', 'tf', 'con', 'dir', 'radius',
             'n', 'baseline', 'baseline_std', 'F0', 'F0_std', 'F1', 'F1_std', 'F2', 'F2_std'
    """

    t = dgr_grp.attrs['time_axis_sec']
    bin_width = np.mean(np.diff(t))
    bl_bins = t <= 0
    res_bins = (t > 0) & (t <= sweep_dur)

    gratings = list(dgr_grp.keys())

    dg_df = pd.DataFrame(columns=['n', 'sf', 'tf', 'dir', 'con', 'radius', 'baseline', 'baseline_std', 'F0', 'F0_std',
                                  'F1', 'F1_std', 'F2', 'F2_std'])

    for i, grating in enumerate(gratings):

        tf = round(dgr_grp[grating].attrs['tf_hz'] * 100.) / 100.
        sf = round(dgr_grp[grating].attrs['sf_cyc_per_deg'] * 1000.) / 1000.
        dire = int(round(180 * dgr_grp[grating].attrs['direction_arc'] // np.pi))
        con = round(dgr_grp[grating].attrs['contrast'] * 100.) / 100.
        radius = round(dgr_grp[grating].attrs['radius_deg'])
        traces = dgr_grp[grating].value / bin_width
        n = traces.shape[0]
        periods = int(round(tf * sweep_dur))

        trace_mean = np.mean(traces, axis=0)

        bls = []
        F0s = []
        F1s = []
        F2s = []
        for trace in traces:
            bls.append(np.mean(trace[bl_bins]))
            curr_trace_res = trace[res_bins]
            curr_har = ta.haramp(trace=curr_trace_res, periods=periods, ceil_f=4)
            F0s.append(curr_har[0])
            F1s.append(curr_har[1])
            F2s.append(curr_har[2])

        bl_std = np.std(bls)
        F0_std = np.std(F0s)
        F1_std = np.std(F1s)
        F2_std = np.std(F2s)

        bl = np.mean(trace_mean[bl_bins])
        trace_mean_res = trace_mean[res_bins]
        har = ta.haramp(trace=trace_mean_res, periods=periods, ceil_f=4)
        F0 = har[0]
        F1 = har[1]
        F2 = har[2]

        dg_df.loc[i] = [n, sf, tf, dire, con, radius, bl, bl_std, F0, F0_std, F1, F1_std, F2, F2_std]

    return dg_df


def plot_dg_df(dgr_df, grid_x_col, grid_y_col, x_col, y_col=('F0', 'F1'), fig=None, fig_kw=None, gridspec_kw=None, ax_kw=None,
               is_shaded=True, shaded_type='std', shade_kw=None, trace_kw=None):

    if shade_kw is None:
        shade_kw = {'lw':0, 'alpha':0.3}

    if trace_kw is None:
        trace_kw = {'lw': 2, 'ls': '-'}

    grid_row_lst = np.unique(dgr_df[grid_y_col])
    grid_row_lst.sort()
    grid_col_lst = np.unique(dgr_df[grid_x_col])
    grid_col_lst.sort()

    if shaded_type == 'sem':
        dgr_df['F0_sem'] = dgr_df['F0_std'] / np.sqrt(dgr_df['n'])
        dgr_df['F1_sem'] = dgr_df['F1_std'] / np.sqrt(dgr_df['n'])
        dgr_df['F2_sem'] = dgr_df['F2_std'] / np.sqrt(dgr_df['n'])
        dgr_df['baseline_sem'] = dgr_df['baseline_std'] / np.sqrt(dgr_df['n'])

    if fig is None:
        fig = plt.figure(**fig_kw)

    axs = pt.grid_axis2(nrows=len(grid_row_lst), ncols=len(grid_col_lst), fig=fig, fig_kw=None,
                        gridspec_kw=gridspec_kw, share_level=2)

    for row_ind, row_value in enumerate(grid_row_lst):
        for col_ind, col_value in enumerate(grid_col_lst):

            curr_title = '{}:{}; {}:{}'.format(grid_x_col, col_value, grid_y_col, row_value)

            curr_df = dgr_df[(dgr_df[grid_y_col] == row_value) & (dgr_df[grid_x_col] == col_value)]
            curr_df = curr_df.sort_values(by=x_col, axis=0)

            curr_ax = axs[row_ind, col_ind]
            if 'F0' in y_col:
                curr_ax.plot(curr_df['dir'], curr_df['F0'], color='#404088', label='F0', **trace_kw)
                if is_shaded:
                    curr_ax.fill_between(curr_df['dir'], curr_df['F0'] - curr_df['F0_' + shaded_type],
                                         curr_df['F0'] + curr_df['F0_' + shaded_type], facecolor='#404088',
                                         **shade_kw)
            if 'F1' in y_col:
                curr_ax.plot(curr_df['dir'], curr_df['F1'], color='#884040', label='F1', **trace_kw)
                if is_shaded:
                    curr_ax.fill_between(curr_df['dir'], curr_df['F1'] - curr_df['F1_' + shaded_type],
                                         curr_df['F1'] + curr_df['F1_' + shaded_type], facecolor='#884040',
                                         **shade_kw)
            if 'F2' in y_col:
                curr_ax.plot(curr_df['dir'], curr_df['F2'], color='#408840', label='F2', **trace_kw)
                if is_shaded:
                    curr_ax.fill_between(curr_df['dir'], curr_df['F2'] - curr_df['F2_' + shaded_type],
                                         curr_df['F2'] + curr_df['F2_' + shaded_type], facecolor='#408840',
                                         **shade_kw)
            if 'baseline' in y_col:
                curr_ax.plot(curr_df['dir'], curr_df['baseline'], color='#888888', label='bl', **trace_kw)
                if is_shaded:
                    curr_ax.fill_between(curr_df['dir'], curr_df['baseline'] - curr_df['baseline_' + shaded_type],
                                         curr_df['baseline'] + curr_df['baseline_' + shaded_type],
                                         facecolor='#888888', **shade_kw)
            curr_ax.set_title(curr_title)
            curr_ax.set_xticks([])
            curr_ax.set_yticks([])
            if row_ind == 0 and col_ind == 0:
                curr_ax.legend()


def generate_strf_from_timestamps(unit_ts, squares_ts_grp, unit_n='', sta_start=-0.055, sta_end=0.205, bin_num=26):
    """
    generate corticalmapping.SingleCellAnalysis.SpatialTemporalReceptiveField object from discrete spike timestamps
    series.

    :param unit_ts: 1d array, spike timestamps, should be monotonic increasing
    :param squares_ts_grp: h5py group object, containing the timestamps of each square displayed, this should be the
                         output of corticalmapping.NwbTools.RecordedFile.analyze_visual_stimuli() function
    :param unit_n: str, name of the unit
    :param sta_start: float, stimulus triggered average start time relative to stimulus onset
    :param sta_end: float, stimulus triggered average end time relative to stimulus onset
    :param bin_num: positive int, number of bins
    :return: corticalmapping.SingleCellAnalysis.SpatialTemporalReceptiveField object
    """

    bin_width = (sta_end - sta_start) / bin_num
    t = np.arange((sta_start + bin_width / 2), (sta_end + bin_width / 2), bin_width)
    t = np.round(t * 100000) / 100000

    all_squares = list(squares_ts_grp.keys())
    traces = []
    locations = []
    signs = []

    for square in all_squares:
        square_grp = squares_ts_grp[square]
        square_ts = square_grp['timestamps'].value
        curr_square_traces = []

        for ts in square_ts:
            _, curr_trace = ta.discrete_cross_correlation([ts], unit_ts, (sta_start, sta_end), bin_num)
            curr_trace = curr_trace.astype(np.float32)
            curr_square_traces.append(curr_trace)

        traces.append(np.array(curr_square_traces))
        locations.append([square_grp['altitude_deg'].value, square_grp['azimuth_deg'].value])
        signs.append(square_grp['sign'].value)

    return sca.SpatialTemporalReceptiveField(locations, signs, traces, t, name=unit_n, trace_data_type='spike_count')


def generate_strf_from_continuous(continuous, continuous_ts, squares_ts_grp, roi_n='', sta_start=0.5, sta_end=1.5):
    """
    generate corticalmapping.SingleCellAnalysis.SpatialTemporalReceptiveField object from continuous trace with
    regular timestamps

    :param continuous: 1d array, signal trace
    :param continuous_ts: 1d array, timestamp series for the continuous, monotonically increasing, should have same
                          size as continuous
    :param squares_ts_grp: h5py group object, containing the timestamps of each square displayed, this should be the
                         output of corticalmapping.NwbTools.RecordedFile.analyze_visual_stimuli() function
    :param roi_n: str, name of the roi
    :param sta_start: float, stimulus triggered average start time relative to stimulus onset
    :param sta_end: float, stimulus triggered average end time relative to stimulus onset
    :param bin_num: positive int, number of bins
    :return: corticalmapping.SingleCellAnalysis.SpatialTemporalReceptiveField object
    """

    mean_frame_dur = np.mean(np.diff(continuous_ts))
    chunk_frame_dur = int(np.ceil((sta_end - sta_start) / mean_frame_dur))
    chunk_frame_start = int(np.floor(sta_start / mean_frame_dur))
    t = (np.arange(chunk_frame_dur) + chunk_frame_start) * mean_frame_dur

    all_squares = list(squares_ts_grp.keys())

    traces = []  # square x trial x t
    locations = []
    signs = []

    for square in all_squares:
        square_grp = squares_ts_grp[square]
        square_ts = square_grp['timestamps'].value
        square_traces = []

        for ts in square_ts:
            curr_frame_start = ta.find_nearest(continuous_ts, ts) + chunk_frame_start
            curr_frame_end = curr_frame_start + chunk_frame_dur
            if curr_frame_start >= 0 and curr_frame_end <= len(continuous_ts):
                square_traces.append(continuous[curr_frame_start: curr_frame_end])

        square_traces = np.array(square_traces)
        square_bl = np.mean(square_traces[:, 0: abs(chunk_frame_start)], axis=1)
        square_bl = np.array([square_bl]).transpose()
        square_dff = (square_traces - square_bl) / square_bl

        traces.append(square_dff)
        locations.append([square_grp['altitude_deg'].value, square_grp['azimuth_deg'].value])
        signs.append(square_grp['sign'].value)

    return sca.SpatialTemporalReceptiveField(locations, signs, traces, t, name=roi_n, trace_data_type='df/f')


if __name__ == '__main__':
    # ===========================================================================
    # dateRecorded = '150930'
    # mouseID = '187474'
    # fileNum = 101
    # displayFolder = r'\\W7DTMJ007LHW\data\sequence_display_log'
    # logPath = findLogPath(date=dateRecorded,mouseID=mouseID,stimulus='KSstimAllDir',userID='',fileNumber=str(fileNum),displayFolder=displayFolder)
    # displayInfo = analysisMappingDisplayLog(logPath)
    # ===========================================================================

    # ===========================================================================
    # inputPath = r"E:\data\python_temp_folder\testNPY.npy"
    # outputPath = r"E:\data\python_temp_folder\testNPY_T.npy"
    # parameterPath = r"E:\data\python_temp_folder\ExampleTraslationParameters.json"
    # inputMov = np.array([[range(100)]*100]*100,dtype=np.uint16)
    # tf.imshow(inputMov,vmin=0,vmax=100)
    # plt.show()
    # np.save(inputPath,inputMov)
    # translateHugeMovieByVasculature(inputPath,outputPath,parameterPath,matchingDecimation=1,referenceDecimation=1,chunkLength=3,verbose=True)
    # outputMov = np.load(outputPath)
    # tf.imshow(outputMov,vmin=0,vmax=100)
    # plt.show()
    # ===========================================================================

    # ===========================================================================
    # movPath = r"\\watersraid\data\Jun\150901-M177931\150901JCamF105_1_1_10.npy"
    # jphysPath = r"\\watersraid\data\Jun\150901-M177931\150901JPhys105"
    # vasMapPaths = [r"\\watersraid\data\Jun\150901-M177931\150901JCamF104"]
    # displayFolder = r'\\W7DTMJ007LHW\data\sequence_display_log'
    # saveFolder = r'E:\data\2015-09-04-150901-M177931-FlashCameraMapping'
    #
    # dateRecorded = '150901'
    # mouseID = '177931'
    # fileNum = '105'
    #
    # temporalDownSampleRate = 10
    #
    # # vasculature map parameters
    # vasMapDtype = np.dtype('<u2')
    # vasMapHeaderLength = 116
    # vasMapTailerLength = 218
    # vasMapColumn = 1024
    # vasMapRow = 1024
    # vasMapFrame = 1
    # vasMapCrop = None
    # vasMapMergeMethod = np.mean #np.median,np.min,np.max
    #
    # #jphys parameters
    # jphysDtype = np.dtype('>f')
    # jphysHeaderLength = 96 # length of the header for each channel
    # jphysChannels = ('photodiode2','read','trigger','photodiode','sweep','visualFrame','runningRef','runningSig','reward','licking')# name of all channels
    # jphysFs = 10000.
    #
    # #photodiode signal parameters
    # pdDigitizeThr=0.9
    # pdFilterSize=0.01
    # pdSegmentThr=0.02
    #
    # #image read signal parameters
    # readThreshold = 3.
    # readOnsetType='raising'
    #
    # #pos map and power map parameters
    # FFTmode='peak'
    # cycles=1
    #
    # #wrap experiment parameters
    # trialNum='4_5'
    # mouseType='Emx1-IRES-Cre;Camk2a-tTA;Ai93(TITL-GCaMP6f)'
    # isAnesthetized=False
    # visualStimType='KSstim'
    # visualStimBackground='gray'
    # analysisParams ={'phaseMapFilterSigma': 1.,
    #                  'signMapFilterSigma': 9.,
    #                  'signMapThr': 0.3,
    #                  'eccMapFilterSigma': 15.0,
    #                  'splitLocalMinCutStep': 10.,
    #                  'closeIter': 3,
    #                  'openIter': 3,
    #                  'dilationIter': 15,
    #                  'borderWidth': 1,
    #                  'smallPatchThr': 100,
    #                  'visualSpacePixelSize': 0.5,
    #                  'visualSpaceCloseIter': 15,
    #                  'splitOverlapThr': 1.1,
    #                  'mergeOverlapThr': 0.1}
    #
    #
    #
    #
    # vasMap = getVasMap(vasMapPaths,
    #                    dtype = vasMapDtype,
    #                    headerLength = vasMapHeaderLength,
    #                    tailerLength = vasMapTailerLength,
    #                    column = vasMapColumn,
    #                    row = vasMapRow,
    #                    frame = vasMapFrame,
    #                    crop = vasMapCrop,
    #                    mergeMethod = vasMapMergeMethod, # np.median, np.min, np.max
    #                    )
    #
    # tf.imsave(os.path.join(saveFolder,dateRecorded+'_M'+mouseID+'_vasMap.tif'),vasMap)
    #
    # _, jphys = ft.importRawNewJPhys(jphysPath,
    #                                 dtype = jphysDtype,
    #                                 headerLength = jphysHeaderLength,
    #                                 channels = jphysChannels,
    #                                 sf = jphysFs)
    #
    # pd = jphys['photodiode']
    #
    # displayOnsets = segmentPhotodiodeSignal(pd,
    #                                                digitizeThr=pdDigitizeThr,
    #                                                filterSize=pdFilterSize,
    #                                                segmentThr=pdSegmentThr,
    #                                                Fs=jphysFs)
    #
    # imgFrameTS = ta.get_onset_timeStamps(jphys['read'],
    #                                    Fs=jphysFs,
    #                                    threshold=readThreshold,
    #                                    onsetType=readOnsetType)
    #
    # logPathList = getlogPathList(date=dateRecorded,
    #                              mouseID=mouseID,
    #                              stimulus='',#string
    #                              userID='',#string
    #                              fileNumber=fileNum,
    #                              displayFolder=displayFolder)
    #
    # displayInfo = analysisMappingDisplayLogs(logPathList)
    #
    # movies, moviesNor = getMappingMovies(movPath=movPath,
    #                                      frameTS=imgFrameTS,
    #                                      displayOnsets=displayOnsets,
    #                                      displayInfo=displayInfo,
    #                                      temporalDownSampleRate=temporalDownSampleRate)
    #
    # for dir,mov in movies.iteritems():
    #     tf.imsave(os.path.join(saveFolder,dateRecorded+'_M'+mouseID+'_aveMov_'+dir+'.tif'),mov)
    # for dir,movNor in moviesNor.iteritems():
    #     tf.imsave(os.path.join(saveFolder,dateRecorded+'_M'+mouseID+'_aveMovNor_'+dir+'.tif'),movNor)
    #
    # del moviesNor
    #
    # altPosMap,aziPosMap,altPowerMap,aziPowerMap = getPositionAndPowerMap(movies=movies,displayInfo=displayInfo,FFTmode=FFTmode,cycles=cycles)
    #
    # del movies
    #
    # f = plt.figure(figsize=(12,10))
    # f.suptitle(dateRecorded+'_M'+mouseID+'_Trial:'+trialNum)
    # ax1 = f.add_subplot(221); fig1 = ax1.imshow(altPosMap, vmin=-30,vmax=50,cmap='hsv',interpolation='nearest')
    # f.colorbar(fig1); ax1.set_title('alt position map')
    # ax2 = f.add_subplot(222); fig2 = ax2.imshow(altPowerMap, vmin=0,vmax=1,cmap='hot',interpolation='nearest')
    # f.colorbar(fig2); ax2.set_title('alt power map')
    # ax3 = f.add_subplot(223); fig3 = ax3.imshow(aziPosMap, vmin=0,vmax=120,cmap='hsv',interpolation='nearest')
    # f.colorbar(fig3); ax3.set_title('azi position map')
    # ax4 = f.add_subplot(224); fig4 = ax4.imshow(aziPowerMap, vmin=0,vmax=1,cmap='hot',interpolation='nearest')
    # f.colorbar(fig4); ax4.set_title('alt power map')
    #
    # f.savefig(os.path.join(saveFolder,dateRecorded+'_M'+mouseID+'_RetinotopicMappingTrial_'+trialNum+'.png'),dpi=300)
    #
    # trialObj = rm.RetinotopicMappingTrial(mouseID=mouseID,
    #                                       dateRecorded=int(dateRecorded),
    #                                       trialNum=trialNum,
    #                                       mouseType=mouseType,
    #                                       visualStimType=visualStimType,
    #                                       visualStimBackground=visualStimBackground,
    #                                       imageExposureTime=np.mean(np.diff(imgFrameTS)),
    #                                       altPosMap=altPosMap,
    #                                       aziPosMap=aziPosMap,
    #                                       altPowerMap=altPowerMap,
    #                                       aziPowerMap=altPowerMap,
    #                                       vasculatureMap=vasMap,
    #                                       isAnesthetized=isAnesthetized,
    #                                       params=analysisParams
    #                                       )
    #
    # trialDict = trialObj.generateTrialDict()
    # ft.saveFile(os.path.join(saveFolder,trialObj.getName()+'.pkl'),trialDict)
    # ===========================================================================

    # ===========================================================================
    input_folder = r"\\aibsdata2\nc-ophys\CorticalMapping\IntrinsicImageData" \
                   r"\170404-M302706\2p_movies\for_segmentation\tempdir"
    c, s = array_to_rois(input_folder=input_folder, overlap_threshold=0.9, neuropil_limit=(5, 10), is_plot=True)
    print(c.shape)
    print(s.shape)

    # ===========================================================================

    print('for debug...')
