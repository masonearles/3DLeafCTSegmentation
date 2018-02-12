# Import libraries
import os
import cv2
import numpy as np
import skimage.io as io
from skimage import transform, img_as_int, img_as_ubyte, img_as_float
from skimage.filters import median, sobel, hessian, gabor, gaussian, scharr
from skimage.segmentation import clear_border
from skimage.morphology import cube, ball, disk
from skimage.util import invert
import scipy as sp
import scipy.ndimage as spim
import scipy.spatial as sptl
from tabulate import tabulate
import pickle
from PIL import Image
from tqdm import tqdm
from numba import jit
import sklearn as skl
from sklearn.preprocessing import LabelEncoder
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.metrics import accuracy_score, confusion_matrix
import matplotlib.pyplot as plt
import pandas as pd
from scipy import misc

# Filter parameters; Label encoder setup
disk_size=5
gauss_sd_list = [2,4,8,16,32,64] #six different filters with different sd for each, big sd = more blurred
gauss_length = 2*len(gauss_sd_list)
hess_range = [4,64]
hess_step = 4
num_feature_layers = 36 # grid and phase recon; plus gaussian blurs; plus hessian filters

# Import label encoder
labenc = LabelEncoder()

def winVar(img, wlen):
    # Variance filter
    wmean, wsqrmean = (cv2.boxFilter(x, -1, (wlen,wlen), borderType=cv2.BORDER_REFLECT)
                       for x in (img, img*img))
    return wsqrmean - wmean*wmean

def RFPredictCTStack(rf_transverse,gridimg_in, phaseimg_in, localthick_cellvein_in, section):
    # Use random forest model to predict entire CT stack on a slice-by-slice basis
    global dist_edge_FL
    dist_edge_FL = []
    # Define distance from lower/upper image boundary
    dist_edge = np.ones(gridimg_in.shape, dtype=np.float64)
    dist_edge[:,(0,1,2,3,4,gridimg_in.shape[1]-4,gridimg_in.shape[1]-3,gridimg_in.shape[1]-2,gridimg_in.shape[1]-1),:] = 0
    dist_edge = transform.rescale(dist_edge, 0.25)
    dist_edge_FL = spim.distance_transform_edt(dist_edge)
    dist_edge_FL = np.multiply(transform.rescale(dist_edge_FL,4),4)
    if dist_edge_FL.shape[1]>gridimg_in.shape[1]:
        dist_edge_FL = dist_edge_FL[:,0:gridimg_in.shape[1],:]

    # Define numpy array for storing class predictions
    RFPredictCTStack_out = np.empty(gridimg_in.shape, dtype=np.float64)

    # Define empty numpy array for feature layers (FL)
    FL = np.empty((gridimg_in.shape[1],gridimg_in.shape[2],num_feature_layers), dtype=np.float64)

    for j in tqdm(range(0,gridimg_in.shape[0])):
        # Populate FL array with feature layers using custom filters, etc.
        FL[:,:,0] = gridimg_in[j,:,:]
        FL[:,:,1] = phaseimg_in[j,:,:]
        FL[:,:,2] = gaussian(FL[:,:,0],8)
        FL[:,:,3] = gaussian(FL[:,:,1],8)
        FL[:,:,4] = gaussian(FL[:,:,0],64)
        FL[:,:,5] = gaussian(FL[:,:,1],64)
        FL[:,:,6] = winVar(FL[:,:,0],9)
        FL[:,:,7] = winVar(FL[:,:,1],9)
        FL[:,:,8] = winVar(FL[:,:,0],18)
        FL[:,:,9] = winVar(FL[:,:,1],18)
        FL[:,:,10] = winVar(FL[:,:,0],36)
        FL[:,:,11] = winVar(FL[:,:,1],36)
        FL[:,:,12] = winVar(FL[:,:,0],72)
        FL[:,:,13] = winVar(FL[:,:,1],72)
        FL[:,:,14] = LoadCTStack(localthick_cellvein_in,j,section)[:,:]
        FL[:,:,15] = dist_edge_FL[j,:,:]
        FL[:,:,16] = gaussian(FL[:,:,0],4)
        FL[:,:,17] = gaussian(FL[:,:,1],4)
        FL[:,:,18] = gaussian(FL[:,:,0],32)
        FL[:,:,19] = gaussian(FL[:,:,1],32)
        FL[:,:,20] = sobel(FL[:,:,0])
        FL[:,:,21] = sobel(FL[:,:,1])
        FL[:,:,22] = gaussian(FL[:,:,20],8)
        FL[:,:,23] = gaussian(FL[:,:,21],8)
        FL[:,:,24] = gaussian(FL[:,:,20],32)
        FL[:,:,25] = gaussian(FL[:,:,21],32)
        FL[:,:,26] = gaussian(FL[:,:,20],64)
        FL[:,:,27] = gaussian(FL[:,:,21],64)
        FL[:,:,28] = gaussian(FL[:,:,20],128)
        FL[:,:,29] = gaussian(FL[:,:,21],128)
        FL[:,:,30] = winVar(FL[:,:,20],32)
        FL[:,:,31] = winVar(FL[:,:,21],32)
        FL[:,:,32] = winVar(FL[:,:,20],64)
        FL[:,:,33] = winVar(FL[:,:,21],64)
        FL[:,:,34] = winVar(FL[:,:,20],128)
        FL[:,:,35] = winVar(FL[:,:,21],128)
        # Collapse training data to two dimensions
        FL_reshape = FL.reshape((-1,FL.shape[2]), order="F")
        class_prediction_transverse = rf_transverse.predict(FL_reshape)
        RFPredictCTStack_out[j,:,:] = class_prediction_transverse.reshape((
            gridimg_in.shape[1],
            gridimg_in.shape[2]),
            order="F")
    return(RFPredictCTStack_out)

def check_images(prediction_prob_imgs,prediction_imgs,observed_imgs,FL_imgs,phaserec_stack):
    # Plot images of class probabilities, predicted classes, observed classes, and feature layer of interest
    #FIX: add error handling here to clean up output
    #FIX: 4/5 images are just black rectangles..address this
    for i in range(0,prediction_imgs.shape[0]):
        # img1 = Image.open(prediction_prob_imgs[i,:,:,1], cmap="RdYlBu")
        location = '../images/qc/'+'predprobIMG'+str(i)+'.tif'
        img1 = img_as_ubyte(prediction_prob_imgs[i,:,:,1])
        io.imsave(location, img1)

        location = '../images/qc/'+'observeIMG'+str(i)+'.tif'
        img2 = (img_as_ubyte(observed_imgs[i,:,:].astype(np.uint64)))*85 #multiply by 85 to get values (in range 0-3) into 8-bit (0-255) distribution
        io.imsave(location, img2)

        location = '../images/qc/'+'predIMG'+str(i)+'.tif'
        img3 = (img_as_ubyte(prediction_imgs[i,:,:].astype(np.uint64)))*85
        io.imsave(location, img3)

        location = '../images/qc/'+'phaserec_stackIMG'+str(i)+'.tif'
        img4 = (img_as_ubyte(phaserec_stack[260,:,:].astype(np.uint64)))*85
        io.imsave(location, img4)

        location = '../images/qc/'+'feature_layerIMG'+str(i)+'.tif'
        img5 = (img_as_ubyte(FL_imgs[0,:,:,26].astype(np.uint64)))*85
        io.imsave(location, img5)
    print("See 'images/qc' folder for quality control images")

def reshape_arrays(class_prediction_prob,class_prediction,Label_test,FL_test,label_stack):
    print("***RESHAPING ARRAYS***")
    # Reshape arrays for plotting images of class probabilities, predicted classes, observed classes, and feature layer of interest
    prediction_prob_imgs = class_prediction_prob.reshape((
        -1,
        label_stack.shape[1],
        label_stack.shape[2],
        4),
        order="F")
    prediction_imgs = class_prediction.reshape((
        -1,
        label_stack.shape[1],
        label_stack.shape[2]),
        order="F")
    observed_imgs = Label_test.reshape((
        -1,
        label_stack.shape[1],
        label_stack.shape[2]),
        order="F")
    FL_imgs = FL_test.reshape((
        -1,
        label_stack.shape[1],
        label_stack.shape[2],
        36),
        order="F")
    return prediction_prob_imgs,prediction_imgs,observed_imgs,FL_imgs

def make_conf_matrix(L_test,class_p):
    # Generate confusion matrix for transverse section
    # FIX: better format the output of confusion matrix to .txt file
    df = pd.crosstab(L_test, class_p, rownames=['Actual'], colnames=['Predicted'])
    print(tabulate(df, headers='keys', tablefmt='pqsl'))
    # convert to np array and then np.savetxt("name", data)
    # npdf = df.values
    # np.savetxt('../results/ConfusionMatrix.txt',npdf,fmt='%.4i',header='Confusion Matrix')
    df.to_csv('../results/ConfusionMatrix.txt',header='Predicted', index='Actual', sep=' ', mode='w')

def make_normconf_matrix(L_test,class_p):
    # Generate normalized confusion matrix for transverse section
    # FIX: better format the output of confusion matrix to .txt file
    df = pd.crosstab(L_test, class_p, rownames=['Actual'], colnames=['Predicted'], normalize='index')
    print(tabulate(df, headers='keys', tablefmt='pqsl'))
    # convert to np array and then np.savetxt("name", data)
    # npdf = df.values
    # np.savetxt('../results/NormalizedConfusionMatrix.txt',npdf,fmt='%.4e',header='Normalized Confusion Matrix')
    df.to_csv('../results/NormalizedConfusionMatrix.txt',header='Predicted', index='Actual', sep=' ', mode='w')

def predict_testset(rf_t,FL_test):
    # predict single slices from dataset
    # Make prediction on test set
    print("***GENERATING PREDICTED STACK***")
    class_prediction = rf_t.predict(FL_test)
    class_prediction_prob = rf_t.predict_proba(FL_test)

    return class_prediction, class_prediction_prob

def print_feature_layers(rf_t):
    # Print feature layer importance
    # See RFLeafSeg module for corresponding feature layer types
    file = open('../results/FeatureLayer.txt','w')
    file.write('Our OOB prediction of accuracy for is: {oob}%'.format(oob=rf_t.oob_score_ * 100)+'\n')
    feature_layers = range(0,len(rf_t.feature_importances_))
    for fl, imp in zip(feature_layers, rf_t.feature_importances_):
        #print('Feature_layer {fl} importance: {imp}'.format(fl=fl, imp=imp))
        file.write('Feature_layer {fl} importance: {imp}'.format(fl=fl, imp=imp)+'\n')
    file.close()

def displayImages_displayDims(gr_s,pr_s,ls,lt_s,gp_train,gp_test,label_train,label_test):
    # FIX: print images to qc
    # #plot some images for QC
    # for i in [label_test,label_train]:
    #     imgA = ls[i,:,:]
    #     imgA = Image.fromarray(imgA)
    #     imgA.show()
    #
    # for i in [gp_train,gp_test]:
    #     io.imshow(gr_s[i,:,:], cmap='gray')
    #     io.show()
    # for i in [gp_train,gp_test]:
    #     io.imshow(pr_s[i,:,:], cmap='gray')
    #     io.show()
    # for i in [gp_train,gp_test]:
    #     io.imshow(lt_s[i,:,:])
    #     io.show()

    #check shapes of stacks to ensure they match
    print(gr_s.shape)
    print(pr_s.shape)
    print(ls.shape)
    print(lt_s.shape)

def LoadCTStack(gridimg_in,sub_slices,section):
    # Define image dimensions
    if(section=="transverse"):
        img_dim1 = gridimg_in.shape[1]
        img_dim2 = gridimg_in.shape[2]
        num_slices = gridimg_in.shape[0]
        rot_i = 1
        rot_j = 2
        num_rot = 0
    if(section=="paradermal"):
        img_dim1 = gridimg_in.shape[1]
        img_dim2 = gridimg_in.shape[0]
        num_slices = gridimg_in.shape[2]
        rot_i = 0
        rot_j = 2
        num_rot = 1
    if(section=="longitudinal"):
        img_dim1 = gridimg_in.shape[0]
        img_dim2 = gridimg_in.shape[2]
        num_slices = gridimg_in.shape[1]
        rot_i = 1
        rot_j = 0
        num_rot = 1

    # Load training label data

    labelimg_in_rot = np.rot90(gridimg_in, k=num_rot, axes=(rot_i,rot_j))
    labelimg_in_rot_sub = labelimg_in_rot[sub_slices,:,:]

    return(labelimg_in_rot_sub)

def GenerateFL2(gridimg_in, phaseimg_in, localthick_cellvein_in, sub_slices, section):
    # Generate feature layers based on grid/phase stacks and local thickness stack
    # Requires five user inputs:
     # 1) grid recon stack (assumes transverse section)
     # 2) phase recon stack (assumes transverse section)
     # 3) local thickness stack (assumes transverse section)
     # 4) list of sub-slices for training/testing
     # 5) section of interest (i.e. transverse, paradermal, or longitudinal)
    # Define image dimensions (img_dim1, img_dim2), number of slices (num_slices), and rotation parameters (rot_i, rot_j, num_rot)
    if(section=="transverse"):
        img_dim1 = gridimg_in.shape[1]
        img_dim2 = gridimg_in.shape[2]
        num_slices = gridimg_in.shape[0]
        rot_i = 1
        rot_j = 2
        num_rot = 0
    if(section=="paradermal"):
        img_dim1 = gridimg_in.shape[1]
        img_dim2 = gridimg_in.shape[0]
        num_slices = gridimg_in.shape[2]
        rot_i = 0
        rot_j = 2
        num_rot = 1
    if(section=="longitudinal"):
        img_dim1 = gridimg_in.shape[0]
        img_dim2 = gridimg_in.shape[2]
        num_slices = gridimg_in.shape[1]
        rot_i = 1
        rot_j = 0
        num_rot = 1

    #match array dimensions again
    gridimg_in, phaseimg_in = match_array_dim(gridimg_in,phaseimg_in)

    #change back 'sub_slices' not 'sub_slicesVAL'...
    # Rotate stacks to correct section view and select subset of slices
    gridimg_in_rot = np.rot90(gridimg_in, k=num_rot, axes=(rot_i,rot_j))
    phaseimg_in_rot = np.rot90(phaseimg_in, k=num_rot, axes=(rot_i,rot_j))
    gridimg_in_rot_sub = gridimg_in_rot[sub_slices,:,:]
    phaseimg_in_rot_sub = phaseimg_in_rot[sub_slices,:,:]

    # Define distance from lower/upper image boundary
    dist_edge = np.ones(gridimg_in.shape)
    dist_edge[:,(0,1,2,3,4,gridimg_in.shape[1]-5,gridimg_in.shape[1]-4,gridimg_in.shape[1]-3,gridimg_in.shape[1]-2,gridimg_in.shape[1]-1),:] = 0
    dist_edge = transform.rescale(dist_edge, 0.25,clip=True,preserve_range=True)
    dist_edge_FL = spim.distance_transform_edt(dist_edge)
    dist_edge_FL = np.multiply(transform.rescale(dist_edge_FL,4,clip=True,preserve_range=True),4)
    if dist_edge_FL.shape[1]>gridimg_in.shape[1]:
        dist_edge_FL = dist_edge_FL[:,0:gridimg_in.shape[1],:]

    # Define empty numpy array for feature layers (FL)
    FL = np.empty((len(sub_slices),img_dim1,img_dim2,num_feature_layers), dtype=np.float64)

    #get rid of '-1'
    # Populate FL array with feature layers using custom filters, etc.
    for i in tqdm(range(0,len(sub_slices))):
        FL[i,:,:,0] = gridimg_in_rot_sub[i,:,:]
        FL[i,:,:,1] = phaseimg_in_rot_sub[i,:,:]
        FL[i,:,:,2] = gaussian(FL[i,:,:,0],8)
        FL[i,:,:,3] = gaussian(FL[i,:,:,1],8)
        FL[i,:,:,4] = gaussian(FL[i,:,:,0],64)
        FL[i,:,:,5] = gaussian(FL[i,:,:,1],64)
        FL[i,:,:,6] = winVar(FL[i,:,:,0],9)
        FL[i,:,:,7] = winVar(FL[i,:,:,1],9)
        FL[i,:,:,8] = winVar(FL[i,:,:,0],18)
        FL[i,:,:,9] = winVar(FL[i,:,:,1],18)
        FL[i,:,:,10] = winVar(FL[i,:,:,0],36)
        FL[i,:,:,11] = winVar(FL[i,:,:,1],36)
        FL[i,:,:,12] = winVar(FL[i,:,:,0],72)
        FL[i,:,:,13] = winVar(FL[i,:,:,1],72)
        FL[i,:,:,14] = LoadCTStack(localthick_cellvein_in, sub_slices, section)[i,:,:] # > 5%
        FL[i,:,:,15] = dist_edge_FL[i,:,:]
        FL[i,:,:,16] = gaussian(FL[i,:,:,0],4)
        FL[i,:,:,17] = gaussian(FL[i,:,:,1],4)
        FL[i,:,:,18] = gaussian(FL[i,:,:,0],32)
        FL[i,:,:,19] = gaussian(FL[i,:,:,1],32)
        FL[i,:,:,20] = sobel(FL[i,:,:,0])
        FL[i,:,:,21] = sobel(FL[i,:,:,1])
        FL[i,:,:,22] = gaussian(FL[i,:,:,20],8)
        FL[i,:,:,23] = gaussian(FL[i,:,:,21],8)
        FL[i,:,:,24] = gaussian(FL[i,:,:,20],32)
        FL[i,:,:,25] = gaussian(FL[i,:,:,21],32)
        FL[i,:,:,26] = gaussian(FL[i,:,:,20],64)
        FL[i,:,:,27] = gaussian(FL[i,:,:,21],64)
        FL[i,:,:,28] = gaussian(FL[i,:,:,20],128)
        FL[i,:,:,29] = gaussian(FL[i,:,:,21],128)
        FL[i,:,:,30] = winVar(FL[i,:,:,20],32)
        FL[i,:,:,31] = winVar(FL[i,:,:,21],32)
        FL[i,:,:,32] = winVar(FL[i,:,:,20],64)
        FL[i,:,:,33] = winVar(FL[i,:,:,21],64)
        FL[i,:,:,34] = winVar(FL[i,:,:,20],128)
        FL[i,:,:,35] = winVar(FL[i,:,:,21],128)


    # Collapse training data to two dimensions
    FL_reshape = FL.reshape((-1,FL.shape[3]), order="F")
    return FL_reshape

def LoadLabelData(gridimg_in,sub_slices,section):
    # Load labeled data stack
    # Define image dimensions
    if(section=="transverse"):
        img_dim1 = gridimg_in.shape[1]
        img_dim2 = gridimg_in.shape[2]
        num_slices = gridimg_in.shape[0]
        rot_i = 1
        rot_j = 2
        num_rot = 0
    if(section=="paradermal"):
        img_dim1 = gridimg_in.shape[1]
        img_dim2 = gridimg_in.shape[0]
        num_slices = gridimg_in.shape[2]
        rot_i = 0
        rot_j = 2
        num_rot = 1
    if(section=="longitudinal"):
        img_dim1 = gridimg_in.shape[0]
        img_dim2 = gridimg_in.shape[2]
        num_slices = gridimg_in.shape[1]
        rot_i = 1
        rot_j = 0
        num_rot = 1

    # Load training label data
    labelimg_in_rot = np.rot90(gridimg_in, k=num_rot, axes=(rot_i,rot_j))
    labelimg_in_rot_sub = labelimg_in_rot[sub_slices,:,:]

    # Collapse label data to a single dimension
    img_label_reshape = labelimg_in_rot_sub.ravel(order="F")

    # Encode labels as categorical variable
    img_label_reshape = labenc.fit_transform(img_label_reshape)
    return(img_label_reshape)

def load_trainmodel():
    print("***LOADING TRAINED MODEL***")
    #load the model from disk
    filename = '../data_settings/RF_model.sav'
    rf = pickle.load(open(filename, 'rb'))
    print("***LOADING FEATURE LAYER ARRAYS***")
    FL_tr = io.imread('../images/FL_train.tif')
    FL_te = io.imread('../images/FL_test.tif')
    print("***LOADING LABEL IMAGE VECTORS***")
    Label_tr = io.imread('../images/Label_train.tif')
    Label_te = io.imread('../images/Label_test.tif')
    return rf,FL_tr,FL_te,Label_tr,Label_te

def save_trainmodel(rf_t,FL_train,FL_test,Label_train,Label_test):
    #Save model to disk; This can be a pretty large file -- ~2 Gb
    print("***SAVING TRAINED MODEL***")
    filename = '../data_settings/RF_model.sav'
    pickle.dump(rf_t, open(filename, 'wb'))
    print("***SAVING FEATURE LAYER ARRAYS***")
    #save training and testing feature layer array
    io.imsave('../images/FL_train.tif',FL_train)
    io.imsave('../images/FL_test.tif',FL_test)
    print("***SAVING LABEL IMAGE VECTORS***")
    #save label image vectors
    io.imsave('../images/Label_train.tif',Label_train)
    io.imsave('../images/Label_test.tif',Label_test)

def train_model(gr_s,pr_s,ls,lt_s,gp_train,gp_test,label_train,label_test):
    print("***GENERATING FEATURE LAYERS***")
    #figure out how to make this step variable--change how many and which filters are used before running, or in input file
    #generate training and testing feature layer array
    FL_train_transverse = GenerateFL2(gr_s, pr_s, lt_s, gp_train, "transverse")
    FL_test_transverse = GenerateFL2(gr_s, pr_s, lt_s, gp_test, "transverse")
    print("***LOAD AND ENCODE LABEL IMAGE VECTORS***")
    # Load and encode label image vectors
    Label_train = LoadLabelData(ls, label_train, "transverse")
    Label_test = LoadLabelData(ls, label_test, "transverse")
    print("***TRAINING MODEL***")
    # Define Random Forest classifier parameters and fit model
    rf_trans = RandomForestClassifier(n_estimators=50, verbose=True, oob_score=True, n_jobs=-1, warm_start=False) #, class_weight="balanced")
    rf_trans = rf_trans.fit(FL_train_transverse, Label_train)

    return rf_trans,FL_train_transverse,FL_test_transverse, Label_train, Label_test

def match_array_dim_label(stack1,stack2):
    #distinct match array dimensions function, to account for label_stack.shape[0]
    if stack1.shape[1]>stack2.shape[1]:
        stack1 = stack1[:,0:stack2.shape[1],:]
    else:
        stack2 = stack2[:,0:stack1.shape[1],:]
    if stack1.shape[2]>stack2.shape[2]:
        stack1 = stack1[:,:,0:stack2.shape[2]]
    else:
        stack2 = stack2[:,:,0:stack1.shape[2]]

    return stack1, stack2

def match_array_dim(stack1,stack2):
    # Match array dimensions
    if stack1.shape[0] > stack2.shape[0]:
        stack1 = stack1[0:stack2.shape[0],:,:]
    else:
        stack2 = stack2[0:stack1.shape[0],:,:]
    if stack1.shape[1] > stack2.shape[1]:
        stack1 = stack1[:,0:stack2.shape[1],:]
    else:
        stack2 = stack2[:,0:stack1.shape[1],:]
    if stack1.shape[2] > stack2.shape[2]:
        stack1 = stack1[:,:,0:stack2.shape[2]]
    else:
        stack2 = stack2[:,:,0:stack1.shape[2]]

    return stack1, stack2

def local_thickness(im):
    # Calculate local thickness; from Porespy library
    if im.ndim == 2:
        from skimage.morphology import square
    dt = spim.distance_transform_edt(im)
    sizes = sp.unique(sp.around(dt, decimals=0))
    im_new = sp.zeros_like(im, dtype=float)
    for r in tqdm(sizes):
        im_temp = dt >= r
        im_temp = spim.distance_transform_edt(~im_temp) <= r
        im_new[im_temp] = r
        #Trim outer edge of features to remove noise
    if im.ndim == 3:
        im_new = spim.binary_erosion(input=im, structure=ball(1))*im_new
    if im.ndim == 2:
        im_new = spim.binary_erosion(input=im, structure=disc(1))*im_new
    return im_new

def localthick_up_save():
    # run local thickness, upsample and save as a .tif stack in images folder
    print("***GENERATING LOCAL THICKNESS STACK***")
    #load thresholded binary downsampled images for local thickness
    GridPhase_invert_ds = io.imread('../images/GridPhase_invert_ds.tif')
    #run local thickness
    local_thick = local_thickness(GridPhase_invert_ds)
    #upsample local_thickness images
    local_thick_upscale = transform.rescale(local_thick, 4, mode='reflect')
    print("***SAVING LOCAL THICKNESS STACK***")
    #write as a .tif file in our images folder
    io.imsave('../images/local_thick_upscale.tif', local_thick_upscale)

def Threshold_GridPhase_invert_down(grid_img, phase_img, Th_grid, Th_phase):
    # Threshold grid and phase images and add the IAS together, invert, downsample and save as .tif stack
    print("***THRESHOLDING IMAGES***")
    tmp = np.zeros(grid_img.shape)
    tmp[grid_img < Th_grid] = 1
    tmp[grid_img >= Th_grid] = 0
    tmp[phase_img < Th_phase] = 1
    #invert
    tmp_invert = invert(tmp)
    #downsample to 25%
    tmp_invert_ds = transform.rescale(tmp_invert, 0.25)
    print("***SAVING IMAGE STACK***")
    #write as a .tif file un our images folder
    io.imsave('../images/GridPhase_invert_ds.tif',tmp_invert_ds)

def openAndReadFile(filename):
    #opens and reads '.txt' file made by user with instructions for program...may execute full process n times
    #initialize empty lists
    gridphase_train_slices_subset = []
    gridphase_test_slices_subset = []
    label_train_slices_subset = []
    label_test_slices_subset = []
    #opens file
    #figure out nice way to handle any errors here
    myFile = open(filename, "r")
	#reads a line
    filepath = str(myFile.readline()) #function to read ONE line at a time
    filepath = filepath.replace('\n','') #strip linebreaks
    grid_name = str(myFile.readline())
    grid_name = grid_name.replace('\n','')
    phase_name = str(myFile.readline())
    phase_name = phase_name.replace('\n','')
    label_name = str(myFile.readline())
    label_name = label_name.replace('\n','')
    Th_grid = float(myFile.readline())
    Th_phase = float(myFile.readline())
    line = myFile.readline().split(",")
    for i in line:
        i = int(i.rstrip('\n'))
        gridphase_train_slices_subset.append(i)
    line = myFile.readline().split(",")
    for i in line:
        i = int(i.rstrip('\n'))
        gridphase_test_slices_subset.append(i)
    line = myFile.readline().split(",")
    for i in line:
        i = int(i.rstrip('\n'))
        label_train_slices_subset.append(i)
    line = myFile.readline().split(",")
    for i in line:
        i = int(i.rstrip('\n'))
        label_test_slices_subset.append(i)
    image_process_bool = str(myFile.readline().rstrip('\n'))
    train_model_bool = str(myFile.readline().rstrip('\n'))
    full_stack_bool = str(myFile.readline().rstrip('\n'))
    # print(filepath)
    # print(grid_name)
    # print(phase_name)
    # print(label_name)
    # print(Th_grid)
    # print(Th_phase)
    # print(gridphase_train_slices_subset)
    # print(gridphase_train_slices_subset[1])
    # print(gridphase_test_slices_subset)
    # print(label_train_slices_subset)
    # print(label_test_slices_subset)
    # print(image_process_bool)
    # print(train_model_bool)
    # print(full_stack_bool)
    #closes the file
    print("File read successfully")
    myFile.close()
    return filepath,grid_name,phase_name,label_name,Th_grid,Th_phase,gridphase_train_slices_subset,gridphase_test_slices_subset,label_train_slices_subset,label_test_slices_subset,image_process_bool,train_model_bool,full_stack_bool

def Load_images(fp,gr_name,pr_name,ls_name):
    #image loading
    # Set path to tiff stacks
    #filepath = '../images/'
    print("***LOADING IMAGE STACKS***")
    # Read gridrec, phaserec, and label tif stacks
    gridrec_stack = io.imread(fp + gr_name)
    phaserec_stack = io.imread(fp + pr_name)
    #Optional image loading, if you need to rotate images
    #label_stack = np.rollaxis(io.imread(filepath + 'label_stack.tif'),2,0)
    label_stack = io.imread(fp + ls_name)
    #Invert my label_stack, uncomment as needed
    #label_stack = invert(label_stack)
    return gridrec_stack, phaserec_stack, label_stack

def performance_metrics(RFpred,test_slices,label_stack,label_slices):
    # FIX: better format the output of confusion matrix to .txt file
    # generate absolute confusion matrix
    conf_matrix = pd.crosstab(RFpred[test_slices,:,:].ravel(order="F"),label_stack[label_slices,:,].ravel(order="F"),rownames=['Actual'], colnames=['Predicted'])
    #df.to_csv('../results/Absolute_performance.txt',header='Predicted', index='Actual', sep=' ', mode='w')
    # generate normalized confusion matrix
    conf_matrix_norm = pd.crosstab(RFpred[test_slices,:,:].ravel(order="F"),label_stack[label_slices,:,].ravel(order="F"), rownames=['Actual'], colnames=['Predicted'], normalize='index')
    total_accuracy = float(np.diag(conf_matrix).sum())/float(RFpred[test_slices,:,:].sum())
    class_precision = np.diag(conf_matrix)/np.sum(conf_matrix,1)
    class_recall = np.diag(conf_matrix)/np.sum(conf_matrix,0)
    print("See 'results' folder for performance metrics")
    # FIX: printing performance metrics arrays in terminal
    print("Total Accuracy\n")
    print(total_accuracy)
    print("Precision\n")
    print(class_precision)
    print("Recall\n")
    print(class_recall)
    np.savetxt('../results/total_accuracy.txt',total_accuracy,fmt='%.4e',header='Total Accuracy')
    np.savetxt('../results/class_precision.txt',class_precision,fmt='%.4e',header='Class Precision')
    np.savetxt('../results/class_recall.txt',class_recall,fmt='%.4e',header='Class Recall')
    # convert to np array and then np.savetxt("name", data)
    # npdf = df.values
    # np.savetxt('../results/ConfusionMatrix.txt',npdf,fmt='%.4i',header='Confusion Matrix')

def load_fullstack(filename):
    print("***LOADING FULL STACK PREDICTION***")
    #load the model from disk
    rf = io.imread('../results/'+filename)
    return rf

def main():
    selection_ = "1"
    print("*******_____STARTED ML_microCT_____*******")
    while selection_ != "3":
        print("*******_____MAIN MENU_____*******")
        print("1. Manual Mode")
        print("2. Read From File Mode")
        print("3. Quit")
        selection_ = str(input("Select an option (type a number, press enter):\n"))
        if selection_=="1":
            selection = "1"
            while selection != "7":
                print("********_____MANUAL MODE MAIN MENU_____********")
                print("1. Image loading and pre-processing")
                print("2. Train model")
                print("3. Examine prediction metrics on training dataset")
                print("4. Predict single slices from test dataset")
                print("5. Predict all slices in 3d microCT stack")
                print("6. Calculate performance metrics")
                print("7. Go back")
                selection = str(input("Select an option (type a number, press enter):\n"))
                if selection=="1": #image loading and pre-processing
                    selection2 = "1"
                    while selection2 != "5":
                        print("********_____IMAGE LOADING AND PRE-PROCESSING MENU_____********")
                        print("1. Load image stacks")
                        print("2. Generate binary threshold image, invert, downsample and save")
                        print("3. Run local thickness algorithm (requires downsampled tif), upsample and save")
                        print("4. Load processed local thickness stack")
                        print("5. Go back")
                        selection2 = str(input("Select an option (type a number, press enter):\n"))
                        if selection2=="1": #load image stacks
                            #manual entry
                            #add error handling here
                            # filepath = raw_input("Enter filepath to .tif stacks, relative to main.py:\n")
                            # grid_name = raw_input("Enter filename of grid reconstruction .tif stack:\n")
                            # phase_name = raw_input("Enter filename of phase reconstruction .tif stack:\n")
                            # label_name = raw_input("Enter filename of labeled .tif stack:\n")
                            filepath = "../images/"
                            grid_name = "gridrec_stack_conc.tif"
                            phase_name = "phaserec_stack_conc.tif"
                            label_name = "label_stack_conc.tif"
                            gridrec_stack, phaserec_stack, label_stack = Load_images(filepath,grid_name,phase_name,label_name)
                        elif selection2=="2": #generate binary threshold image, invert, downsample and save
                            Th_grid = raw_input("Enter subjective lower threshold value for grid-phase reconstruction images, determined in FIJI.\n")
                            Th_grid = float(Th_grid)
                            Th_phase = raw_input("Enter subjective upper threshold value for grid-phase reconstruction images, determined in FIJI.\n")
                            Th_phase = float(Th_phase)
                            # Th_grid = -22.09
                            # Th_phase = 0.6
                            Threshold_GridPhase_invert_down(gridrec_stack,phaserec_stack,Th_grid,Th_phase)
                        elif selection2=="3": #run local thickness, upsample, save
                            localthick_up_save()
                        elif selection2=="4": #load processed local thickness stack and match array dimensions
                            print("***LOADING LOCAL THICKNESS STACK***")
                            localthick_stack = io.imread('../images/local_thick_upscale.tif')
                            # Match array dimensions to correct for resolution loss due to downsampling when generating local thickness
                            gridrec_stack, localthick_stack = match_array_dim(gridrec_stack,localthick_stack)
                            phaserec_stack, localthick_stack = match_array_dim(phaserec_stack,localthick_stack)
                            label_stack, localthick_stack = match_array_dim_label(label_stack,localthick_stack)
                        elif selection2=="5": #go back one step
                            print("Going back one step...")
                        else:
                            print("Not a valid choice.")
                elif selection=="2": #train model
                    selection3="1"
                    while selection3 != "6":
                        print("********_____TRAIN MODEL MENU_____********")
                        print("1. Define image subsets for training and testing")
                        print("2. Display some images from each stack and stack dimensions for QC")
                        print("3. Train model")
                        print("4. Save trained model and feature layer arrays")
                        print("5. Load trained model and feature layer arrays")
                        print("6. Go back")
                        selection3 = str(input("Select an option (type a number, press enter):\n"))
                        if selection3=="1": #define image subsets for training and testing
                            gridphase_train_slices_subset = []
                            gridphase_test_slices_subset = []
                            label_train_slices_subset = []
                            label_test_slices_subset = []
                            print("***DEFINING IMAGE SUBSETS***")
                            count = int(input("Enter number of slices in grid-phase train/test subset:\n"))
                            for i in range(0,count):
                                hold = int(raw_input("Enter number for grid-phase TRAINING slice subset, one at a time, in order\n(you will be prompted again for subsequent numbers):\n"))
                                gridphase_train_slices_subset.append(hold)
                            for i in range(0,count):
                                hold2 = raw_input("Enter number for grid-phase TESTING slice(s) subset, one at a time, in order\n(you will be prompted again for subsequent numbers):\n")
                                hold2 = int(hold2)
                                gridphase_test_slices_subset.append(hold2)
                            count = int(input("Enter number of slices in labeled images train/test subset:\n"))
                            for i in range (0,count):
                                hold3 = raw_input("Enter number for labeled images TRAINING slice(s) subset, one at a time, in order\n(you will be prompted again for subsequent numbers):\n")
                                hold3 = int(hold3)
                                label_train_slices_subset.append(hold3)
                            for i in range (0,count):
                                hold4 = raw_input("Enter number for labeled images TESTING slice(s) subset, one at a time, in order\n(you will be prompted again for subsequent numbers):\n")
                                hold4 = int(hold4)
                                label_test_slices_subset.append(hold4)
                            # gridphase_train_slices_subset = [45]
                            # gridphase_test_slices_subset = [245]
                            # label_train_slices_subset = [1]
                            # label_test_slices_subset = [0]
                        elif selection3=="2": #plot some images and stack dimensions
                            displayImages_displayDims(gridrec_stack,phaserec_stack,label_stack,localthick_stack,gridphase_train_slices_subset,gridphase_test_slices_subset,label_train_slices_subset,label_test_slices_subset)
                        elif selection3=="3": #train model
                            rf_transverse,FL_train,FL_test,Label_train,Label_test = train_model(gridrec_stack,phaserec_stack,label_stack,localthick_stack,gridphase_train_slices_subset,gridphase_test_slices_subset,label_train_slices_subset,label_test_slices_subset)
                        elif selection3=="4": #save trained model and other arrays from step 3 to disk
                            save_trainmodel(rf_transverse,FL_train,FL_test,Label_train,Label_test)
                        elif selection3=="5": #load trained model and other arrays from step 4, to skip 1-4 if already ran
                            rf_transverse,FL_train,FL_test,Label_train,Label_test = load_trainmodel()
                        elif selection3=="6": #go back one step
                            print("Going back one step...")
                        else:
                            print("Not a valid choice.")
                elif selection=="3": #examine prediction metrics on training dataset
                    # Print out of bag precition accuracy
                    hold = "1"
                    print('Our Out Of Box prediction of accuracy is: {oob}%'.format(oob=rf_transverse.oob_score_ * 100))
                    print("Would you like to print feature layer importance?")
                    hold = str(input("Enter 1 for yes, or 2 for no:\n"))
                    if hold == "1":
                        print_feature_layers(rf_transverse)
                        print("See results folder for feature layer importance")
                    else:
                        print("Okay. Going back.")
                elif selection=="4": #predict single slices
                    #print("You selected option 4")
                    selection4="1"
                    while selection4 != "4":
                        print("********_____SINGLE SLICE PREDICTIONS MENU_____********")
                        print("1. Predict single slices from test dataset")
                        print("2. Generate confusion matrices")
                        print("3. Plot images")
                        print("4. Go back")
                        selection4 = str(input("Select an option (type a number, press enter):\n"))
                        if selection4=="1": #predict single slices from test dataset
                            class_prediction, class_prediction_prob = predict_testset(rf_transverse,FL_test)
                        elif selection4=="2": #generate confusion matrices
                            print("Confusion Matrix")
                            make_conf_matrix(Label_test,class_prediction)
                            print("___________________________________________")
                            print("Normalized Confusion Matrix")
                            make_normconf_matrix(Label_test,class_prediction)
                        elif selection4=="3": #plot images
                            print("Quality Control images saved to 'images/qc' folder.")
                            prediction_prob_imgs,prediction_imgs,observed_imgs,FL_imgs = reshape_arrays(class_prediction_prob,class_prediction,Label_test,FL_test,label_stack)
                            check_images(prediction_prob_imgs,prediction_imgs,observed_imgs,FL_imgs,phaserec_stack)
                        elif selection4=="4": #go back one step
                            print("Going back one step...")
                        else:
                            print("Not a valid choice.")
                elif selection=="5": #predict all slices in 3d stack
                    selection5="1"
                    while selection5 != "3":
                        print("********_____FULL STACK PREDICTIONS MENU_____********")
                        print("1. Predict full stack")
                        print("2. Write stack as .tif file")
                        print("3. Load existing full stack prediction")
                        print("4. Go back")
                        selection5 = str(input("Select an option (type a number, press enter):\n"))
                        if selection5=="1": #predict full stack
                            print("***PREDICTING FULL STACK***")
                            RFPredictCTStack_out = RFPredictCTStack(rf_transverse,gridrec_stack,phaserec_stack,localthick_stack,"transverse")
                        elif selection5=="2": #write stack as a tiff file
                            print("***SAVING PREDICTED STACK***\nSee 'results' folder")
                            name = raw_input("Enter filename suffix for fullstack prediction:\n(use letters, numbers or underscores; no spaces)\n")
                            io.imsave("../results/fullstack_pred"+name+".tif", img_as_int(RFPredictCTStack_out/6))
                        elif selection5=="3": #load full stack prediction
                            # print("You selected option 3. This steps needs updates!")
                            # FIX: throws error due to unsigned int16 format
                            name2 = raw_input("Enter filename for existing fullstack prediction (located in 'results' folder):\n")
                            RFPredictCTStack_out = load_fullstack(name2)
                        elif selection5=="4": #go back one step
                            print("Going back one step...")
                        else:
                            print("Not a valid choice.")
                elif selection=="6": #performance metrics
                    print("You selected option 6. This step needs updating!")
                    # FIX
                    # performance_metrics(RFPredictCTStack_out,gridphase_test_slices_subset,label_stack,label_test_slices_subset)
                    # print("This step needs updating!")
                elif selection=="7": #go back one step
                    print("Going back one step...")
                else:
                    print("Not a valid choice.")
        elif selection_=="2":
            # FIX: step needs batch run capability
            # idea: add promt for number of batches(==counter), then prompt for each batche's instruction text file, at end counter--; have all in while loop (while counter>0...)
            print("Will read values from your text file, then export results to 'results' folder.")
            #print("For viewing images, confusion matrices, predicted images and other information see results folder.\n")
            print("***FIRST YOU MUST:***")
            # add error handling here
            filename = raw_input("Enter filename of your text file: (for example, enter exactly: filename.txt)\n")
            #read input file and define variables
            filepath,grid_name,phase_name,label_name,Th_grid,Th_phase,gridphase_train_slices_subset,gridphase_test_slices_subset,label_train_slices_subset,label_test_slices_subset,image_process_bool,train_model_bool,full_stack_bool = openAndReadFile("../data_settings/"+filename)
            #load images
            gridrec_stack, phaserec_stack, label_stack = Load_images(filepath,grid_name,phase_name,label_name)
            if image_process_bool=="1":
                print("IMAGE PROCESSING")
                #generate binary threshold image, invert, downsample and save
                Threshold_GridPhase_invert_down(gridrec_stack,phaserec_stack,Th_grid,Th_phase)
                #run local thickness, upsample, save
                localthick_up_save()
            else:
                print("SKIPPED IMAGE PROCESSING")
                #load processed local thickness stack and match array dimensions
            print("***LOADING LOCAL THICKNESS STACK***")
            localthick_stack = io.imread('../images/local_thick_upscale.tif')
            # Match array dimensions to correct for resolution loss due to downsampling when generating local thickness
            gridrec_stack, localthick_stack = match_array_dim(gridrec_stack,localthick_stack)
            phaserec_stack, localthick_stack = match_array_dim(phaserec_stack,localthick_stack)
            label_stack, localthick_stack = match_array_dim_label(label_stack,localthick_stack)
            #this is just for peoples' feelings, these are defined way earlier...in the .txt file
            print("***DEFINING IMAGE SUBSETS***")
            if train_model_bool=="1":
                #train model and return lots of stuff we need
                rf_transverse,FL_train,FL_test,Label_train,Label_test = train_model(gridrec_stack,phaserec_stack,label_stack,localthick_stack,gridphase_train_slices_subset,gridphase_test_slices_subset,label_train_slices_subset,label_test_slices_subset)
                #save trained model and other arrays from step 3 to disk
                save_trainmodel(rf_transverse,FL_train,FL_test,Label_train,Label_test)
            else:
                print("SKIPPED TRAINING MODEL")
                #load trained model and other arrays we need
                rf_transverse,FL_train,FL_test,Label_train,Label_test = load_trainmodel()
            #predict single slices from test dataset
            class_prediction, class_prediction_prob = predict_testset(rf_transverse,FL_test)
            # Print out of bag precition accuracy and feature layer importance to results folder
            print_feature_layers(rf_transverse)
            print("Confusion Matrix")
            make_conf_matrix(Label_test,class_prediction)
            print("___________________________________________")
            print("Normalized Confusion Matrix")
            make_normconf_matrix(Label_test,class_prediction)
            #plot images
            print("This step needs updating!")
            #reshape arrays
            prediction_prob_imgs,prediction_imgs,observed_imgs,FL_imgs = reshape_arrays(class_prediction_prob,class_prediction,Label_test,FL_test,label_stack)
            #print images to file
            check_images(prediction_prob_imgs,prediction_imgs,observed_imgs,FL_imgs,phaserec_stack)
            if full_stack_bool=="1":
                #predict full stack
                print("***PREDICTING FULL STACK***")
                RFPredictCTStack_out = RFPredictCTStack(rf_transverse,gridrec_stack, phaserec_stack, localthick_stack,"transverse")
                #save predicted full stack
                print("***SAVING PREDICTED STACK***\nSee 'results' folder")
                # hardcoded division number_of_classes right now, FIX
                io.imsave("../results/fullstack_pred.tif", img_as_int(RFPredictCTStack_out/6))
                # performance_metrics(RFPredictCTStack_out,gridphase_test_slices_subset,label_stack,label_test_slices_subset)
            else:
                print("SKIPPED FULL STACK PREDICTION AND PERFORMANCE METRICS")
        elif selection_=="3":
            print("Session Ended")
        else:
            print("Not a valid choice.")

if __name__ == '__main__':
    main()