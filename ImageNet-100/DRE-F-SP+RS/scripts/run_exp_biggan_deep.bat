@echo off

set ROOT_PATH="./ImageNet-100/DRE-F-SP+RS"
set DATA_PATH="./datasets/ImageNet-100"
set GAN_CKPT_PATH="./ImageNet-100/eval_and_gan_ckpts/BigGAN_deep_96K/G_ema.pth"
set EVAL_CKPT_PATH="./ImageNet-100/eval_and_gan_ckpts/ckpt_PreCNNForEval_InceptionV3_epoch_50_SEED_2021_Transformation_True_finetuned.pth"

set SEED=2021
set GAN_NET="BigGANdeep"
set DRE_PRECNN="ResNet34"
set DRE_PRECNN_EPOCHS=350
set DRE_PRECNN_BS=256
set DRE_DR="MLP5"
set DRE_DR_EPOCHS=400
set DRE_DR_LR_BASE=1e-4
set DRE_DR_BS=256
set DRE_DR_LAMBDA=0.01

set SAMP_NROUNDS=1
set SAMP_BS=100
set SAMP_BURNIN=5000
set SAMP_NFAKE_PER_CLASS=1000

@REM DRE-F-SP
python main.py ^
    --root_path %ROOT_PATH% --data_path %DATA_PATH% --seed %SEED% ^
    --gan_net %GAN_NET% --gan_ckpt_path %GAN_CKPT_PATH% ^
    --dre_precnn_net %DRE_PRECNN% --dre_precnn_epochs %DRE_PRECNN_EPOCHS% --dre_precnn_resume_epoch 0 ^
    --dre_precnn_lr_base 0.1 --dre_precnn_lr_decay_factor 0.1 --dre_precnn_lr_decay_epochs "150_250" ^
    --dre_precnn_batch_size_train %DRE_PRECNN_BS% --dre_precnn_weight_decay 1e-4 --dre_precnn_transform ^
    --dre_net %DRE_DR% --dre_epochs %DRE_DR_EPOCHS% --dre_resume_epoch 0 ^
    --dre_lr_base %DRE_DR_LR_BASE% --dre_batch_size %DRE_DR_BS% --dre_lambda %DRE_DR_LAMBDA% ^
    --dre_lr_decay_factor 0.1 --dre_lr_decay_epochs "150_250" ^
    --subsampling ^
    --samp_round %SAMP_NROUNDS% --samp_batch_size %SAMP_BS% --samp_burnin_size %SAMP_BURNIN% ^
    --samp_nfake_per_class %SAMP_NFAKE_PER_CLASS% --samp_dump_fake_data ^
    --eval --eval_FID_batch_size 100 ^
    --eval_ckpt_path %EVAL_CKPT_PATH% --inception_from_scratch ^ %*



